"""Parser for text copied from a HouseSigma listing page."""
from __future__ import annotations

import re

from database.enums import ListingStatus
from parsers.common import (
    ParsedListing, clean_lines, event_to_status, find_labelled, parse_iso_date, put,
)
from utils.normalization import (
    OutOfScopeProperty, classify_property_type, parse_bedrooms, parse_money,
    parse_size_range, split_unit_address,
)

SITE = "HouseSigma"

KNOWN_LABELS = {
    "tax", "property type", "maintenance", "included utility", "building age", "size", "parking",
    "locker", "basement", "listing #", "data source", "predicted days on market",
    "listing brokerage", "days on market", "property days on market", "status change",
    "listed on", "updated on", "market demand", "description",
}

_HISTORY_ROW = re.compile(
    r"(?P<start>\d{4}-\d{2}-\d{2})(?:\s+(?P<end>\d{4}-\d{2}-\d{2}))?\s+\$\s?(?P<price>[\d,]+)"
    r"\s+(?P<event>[A-Za-z][A-Za-z ]*?)\s+(?P<lid>[A-Z]{1,2}\d{5,})"
)
_HISTORY_HEADER_CELLS = {"date end", "price", "event", "listing id"}
_HISTORY_END_MARKERS = {"show more", "key facts", "no price changes on this listing"}
_MONTH_HEADER = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December) \d{4}$"
)
_DISTANCE = re.compile(r"^\d+(\.\d+)?\s*(km|m)$", re.I)


def _description(lines: list[str]) -> tuple[str | None, bool, bool]:
    """Text after the 'Description:' tabs. Returns (text, tabs_seen, original_confirmed).

    On a saved page the first block is the original listing text and ends at 'Show More';
    the AI summary follows. On pasted text there is no such marker.
    """
    try:
        i = next(k for k, l in enumerate(lines) if re.fullmatch(r"Description\s*:?", l, re.I))
    except StopIteration:
        return None, False, False
    i += 1
    tabs_seen = False
    while i < len(lines) and lines[i].lower() in {"original", "summary", "ai"}:
        tabs_seen = True
        i += 1
    chunk, confirmed = [], False
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.lower() == "show more":
            confirmed = bool(chunk) and tabs_seen
            break
        if _MONTH_HEADER.match(line) or _DISTANCE.match(nxt):
            break
        chunk.append(line)
        i += 1
    return ("\n".join(chunk) or None), tabs_seen, confirmed


def _history(lines: list[str]) -> list[dict]:
    """Listing-history table, whether each row is one line (pasted) or one cell per line (saved page)."""
    start = next((i for i, l in enumerate(lines) if l.lower().startswith("date start")), None)
    if start is None:
        return []
    i = start + 1
    if len(lines[start].split()) <= 2:           # header cells on their own lines
        while i < len(lines) and lines[i].lower() in _HISTORY_HEADER_CELLS:
            i += 1
    end = len(lines)
    for j in range(i, len(lines)):
        if lines[j].lower() in _HISTORY_END_MARKERS:
            end = j
            break
    region = " ".join(lines[i:end])
    rows = []
    for m in _HISTORY_ROW.finditer(region):
        event = m.group("event").strip()
        rows.append({
            "observed_on": parse_iso_date(m.group("start")),
            "ended_on": parse_iso_date(m.group("end")),
            "asking_price": parse_money(m.group("price")),
            "listing_status": event_to_status(event),
            "event": event,
            "listing_id": m.group("lid"),
            "note": f"HouseSigma history: listing {m.group('lid')} {m.group('start')}"
                    + (f" to {m.group('end')}" if m.group("end") else "") + f", {event}",
        })
    return rows


def parse(text: str) -> ParsedListing:
    lines = clean_lines(text)
    out = ParsedListing(source=SITE)
    f, feat = out.fields, out.features

    # ---- header: anchored on the "Listed for" line (not on fixed line numbers)
    anchor = next((i for i, l in enumerate(lines) if re.match(r"^Listed for\b", l, re.I)), None)
    scan_from = max((anchor or 0) - 10, 0)
    for i in range(scan_from, min(scan_from + 40, len(lines))):
        one = re.match(r"^(?P<city>[^/$:\d]+?)\s*/\s*(?P<nbhd>[^$:\d]+)$", lines[i])
        three = (lines[i + 1] == "/" and i + 2 < len(lines)) if i + 1 < len(lines) else False
        if one:
            put(f, "city", one.group("city").strip())
            put(f, "neighbourhood", one.group("nbhd").strip())
            break
        if three and not re.search(r"\d", lines[i] + lines[i + 2]):
            put(f, "city", lines[i].strip())
            put(f, "neighbourhood", lines[i + 2].strip())
            break
    if anchor is not None:
        inline = re.match(r"^Listed for:?\s*(\$\s*[\d,.]+)", lines[anchor], re.I)
        price_text = inline.group(1) if inline else (lines[anchor + 1] if anchor + 1 < len(lines) else "")
        if re.match(r"^\$\s*[\d,.]+", price_text):
            put(f, "asking_price", parse_money(price_text))
    window = lines[anchor: anchor + 30] if anchor is not None else lines[:40]
    for line in window:
        m = re.match(r"^(\d+(?:\s*\+\s*\d+)?)\s+Bedrooms?$", line, re.I)
        if m and "bedrooms" not in f:
            b = parse_bedrooms(m.group(1))
            if b:
                put(f, "bedrooms", b[0])
                put(f, "bedrooms_extra", b[1])
        m = re.match(r"^(\d+)\s+Bathrooms?$", line, re.I)
        if m and "bathrooms" not in f:
            put(f, "bathrooms", int(m.group(1)))
        m = re.match(r"^(\d+)\s+Garages?$", line, re.I)
        if m and "garage_count" not in out.extras:
            out.extras["garage_count"] = int(m.group(1))

    # ---- address
    for line in lines:
        m = re.match(r"^Buy/sell history for (?P<addr>.+?)\s*\((?P<ptype>[^)]+)\)\s*$", line, re.I)
        if m:
            full = m.group("addr")
            street_part, _, city = full.rpartition(",")
            if street_part:
                unit, street = split_unit_address(street_part.strip())
                put(f, "city", city.strip()) if "city" not in f else None
            else:
                unit, street = split_unit_address(full)
            put(f, "unit", unit)
            put(f, "address", street)
            break
    for line in lines:
        m = re.match(r"^Key facts for (?P<rest>.+?)\.?$", line, re.I)
        if m:
            parts = [p.strip() for p in m.group("rest").split(",")]
            if "address" not in f:
                unit, street = split_unit_address(parts[0])
                put(f, "unit", unit)
                put(f, "address", street)
            if len(parts) >= 3:
                put(f, "neighbourhood", parts[1]) if "neighbourhood" not in f else None
                put(f, "city", parts[-1]) if "city" not in f else None
            elif len(parts) == 2:
                put(f, "city", parts[1]) if "city" not in f else None
            break

    # ---- key facts
    def val(label: str) -> str | None:
        return find_labelled(lines, label, KNOWN_LABELS)

    tax = val("Tax")
    if tax:
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*/\s*(\d{4})", tax)
        if m:
            put(f, "listed_annual_property_tax", parse_money(m.group(1)))
            put(f, "listed_tax_year", int(m.group(2)))

    ptype = val("Property Type")
    try:
        classified = classify_property_type(ptype.split(",")[0]) if ptype else None
        put(f, "property_type", classified)
    except OutOfScopeProperty as exc:
        out.out_of_scope = str(exc)
    if ptype and "," in ptype:
        out.extras["storeys_text"] = ptype.split(",", 1)[1].strip()

    maint = val("Maintenance")
    if maint:
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*/\s*month", maint, re.I)
        if m:
            put(f, "maintenance_fee", parse_money(m.group(1)))
        else:
            out.notes.append(f"{SITE}: maintenance text '{maint}' was not recognized as a monthly fee.")
    inc = val("Included Utility")
    if inc:
        out.extras["maintenance_includes"] = inc

    size = val("Size")
    if size:
        rng = parse_size_range(size)
        if rng:
            put(f, "interior_size_min", rng[0])
            put(f, "interior_size_max", rng[1])
        else:
            out.notes.append(f"{SITE}: size '{size}' could not be read as square feet.")

    parking = val("Parking")
    if parking:
        put(f, "parking", parking)
        m = re.search(r"total\s+(\d+)\s+park", parking, re.I)
        if m:
            put(f, "parking_spaces", int(m.group(1)))
    put(f, "locker", val("Locker"))
    bsmt = val("Basement")
    if bsmt:
        put(feat, "basement", bsmt)
    put(f, "listing_number", val("Listing #"))
    put(f, "listing_date", parse_iso_date(val("Listed on")))
    upd = parse_iso_date(val("Updated on"))
    if upd:
        out.extras["listing_updated_on"] = upd.isoformat()
    age = val("Building Age")
    if age and age != "-":
        out.extras["building_age"] = age
    brokerage = val("Listing Brokerage")
    if brokerage:
        out.extras["listing_brokerage"] = brokerage

    # ---- listing history table (one row per listing period)
    out.history = _history(lines)
    if out.history:
        latest = max(out.history, key=lambda r: r["observed_on"])
        if latest["listing_status"] is not None:
            put(f, "listing_status", latest["listing_status"])
        if "asking_price" not in f and latest["listing_status"] == ListingStatus.FOR_SALE:
            put(f, "asking_price", latest["asking_price"])

    # ---- description
    desc, tabs_seen, confirmed = _description(lines)
    put(f, "description", desc)
    if desc and tabs_seen and not confirmed:
        out.notes.append(
            f"{SITE} shows Original / Summary / AI tabs. The description may be HouseSigma's own "
            "summary, not the agent's original text; prefer the original wording if you have it."
        )

    if not f:
        out.notes.append(f"{SITE}: no recognizable listing facts were found in this text.")
    return out
