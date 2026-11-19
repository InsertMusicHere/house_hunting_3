"""Parser for text copied from a Realtor.ca listing page."""
from __future__ import annotations

import re

from database.enums import PropertyType
from parsers.common import ParsedListing, clean_lines, find_labelled, index_of, put
from utils.normalization import (
    OutOfScopeProperty, classify_property_type, normalize_postal_code, parse_bedrooms,
    parse_money, parse_size_range, smart_title, split_unit_address,
)

SITE = "Realtor.ca"

KNOWN_LABELS = {
    "property type", "building type", "storeys", "square footage", "community name", "title",
    "annual property taxes", "parking type", "time on realtor.ca", "appliances included",
    "flooring", "basement type", "fire protection", "building amenities", "structures", "cooling",
    "heating type", "exterior finish", "pool type", "community features", "amenities nearby",
    "maintenance fees", "maintenance fees include", "maintenance management company",
    "total parking spaces", "location description", "listing description", "property summary",
    "building", "bedrooms", "bathrooms", "above grade", "below grade", "total", "partial",
    "interior features", "heating & cooling", "exterior features", "neighbourhood features",
    "maintenance or condo information", "parking", "measurements", "rooms", "metric", "imperial",
    "highlights", "neighbourhood", "statistics", "calculators",
}

_CITY_LINE = re.compile(
    r"^(?P<city>[^(),]+?)\s*(?:\((?P<nbhd>[^)]+)\))?,\s*Ontario"
    r"(?:\s+(?P<postal>[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d))?\s*$"
)
_METRIC = re.compile(r"^\d+(?:\.\d+)?\s*m(?:\s*x\s*\d+(?:\.\d+)?\s*m)?$", re.I)
_ROOMS_STOP = {"neighbourhood", "payment", "affordability", "land transfer tax",
               "renewal or refinance", "share feedback", "demographics"}
_DIMS = re.compile(r"^\d+(?:\s*ft)?(?:\s*,\s*\d+\s*in)?(?:\s*x\s*\d+(?:\s*ft)?(?:\s*,\s*\d+\s*in)?)?$", re.I)
_DIMS_FT = re.compile(r"\d+\s*ft", re.I)
_LEVEL = re.compile(
    r"^(?P<level>(?:main|lower|upper|second|third|fourth|ground|in-between)\s+level)\b\s*(?P<room>.*)$", re.I)
_SINGLE_LEVELS = {"basement", "sub-basement", "loft"}
_SPLIT_LIST = re.compile(r"\s*,\s*")


def _description(lines: list[str]) -> str | None:
    i = index_of(lines, "Listing Description")
    if i is None:
        return None
    chunk = []
    for line in lines[i + 1:]:
        if line.lower() in {"location description", "property summary"}:
            break
        chunk.append(line)
    return "\n".join(chunk) or None


def _rooms(lines: list[str]) -> list[dict]:
    """Room sizes. Handles pasted text (imperial only) and saved pages (metric line then imperial line)."""
    i = index_of(lines, "Rooms")
    if i is None:
        return []
    rooms, level, pending, metric = [], None, None, None
    for line in lines[i + 1:i + 150]:
        low = line.lower()
        if low in _ROOMS_STOP:
            break
        if low in {"metric", "imperial"} or not re.search(r"[A-Za-z0-9]", line):
            continue  # toggle labels and decoration such as an em dash
        if _METRIC.match(line):
            metric = line
            continue
        if _DIMS_FT.search(line) and _DIMS.match(line):
            if pending:
                rooms.append({"level": level, "room": pending, "dimensions": line,
                              **({"dimensions_metric": metric} if metric else {})})
                pending, metric = None, None
            continue
        m = _LEVEL.match(line)
        if m:
            level = m.group("level").title()
            pending = m.group("room").strip() or None
        elif low in _SINGLE_LEVELS:
            level, pending = line.title(), None
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9 ,/&'()-]{1,60}", line):
            pending = line
    return rooms


def parse(text: str) -> ParsedListing:
    lines = clean_lines(text)
    out = ParsedListing(source=SITE)
    f, feat = out.fields, out.features

    def val(label: str) -> str | None:
        return find_labelled(lines, label, KNOWN_LABELS)

    # ---- header: anchored on the first "City (Neighbourhood), Ontario A1A 1A1" line
    city_idx = next((i for i, l in enumerate(lines) if _CITY_LINE.match(l)), None)
    price_idx = None
    if city_idx is not None:
        m = _CITY_LINE.match(lines[city_idx])
        put(f, "city", m.group("city").strip())
        put(f, "neighbourhood", (m.group("nbhd") or "").strip() or None)
        put(f, "postal_code", normalize_postal_code(m.group("postal")) if m.group("postal") else None)
        for i in range(city_idx - 1, max(city_idx - 40, -1), -1):
            pm = re.fullmatch(r"\$\s?([\d,]+(?:\.\d{1,2})?)", lines[i])
            if pm:
                put(f, "asking_price", parse_money(pm.group(1)))
                price_idx = i
                break
        candidate = lines[city_idx - 1] if city_idx >= 1 else ""
        if city_idx - 1 != price_idx and re.search(r"\d", candidate) and not candidate.startswith("$"):
            unit, street = split_unit_address(candidate)
            put(f, "unit", unit)
            put(f, "address", street)

    m = re.search(r"MLS[^A-Za-z0-9]{0,8}(?:Number|#)\s*:?\s*([A-Za-z]{0,2}\d+)", "\n".join(lines))
    if m:
        put(f, "listing_number", m.group(1))

    # ---- header counts shortly after the address block: "3" / "Bedrooms", "1400 - 1599" / "Square Feet"
    start = city_idx or 0
    for i in range(start, min(start + 60, len(lines))):
        line = lines[i]
        nxt = lines[i + 1].lower() if i + 1 < len(lines) else ""
        if nxt in {"bedrooms", "bedroom"} and re.fullmatch(r"\d+(?:\s*\+\s*\d+)?", line) and "bedrooms" not in f:
            b = parse_bedrooms(line)
            if b:
                put(f, "bedrooms", b[0])
                put(f, "bedrooms_extra", b[1])
        elif nxt in {"bathrooms", "bathroom"} and re.fullmatch(r"\d+", line) and "bathrooms" not in f:
            put(f, "bathrooms", int(line))
        elif nxt == "square feet" and re.fullmatch(r"[\d,]+(?:\s*-\s*[\d,]+)?", line) and "interior_size_min" not in f:
            rng = parse_size_range(line)
            if rng:
                put(f, "interior_size_min", rng[0])
                put(f, "interior_size_max", rng[1])

    # ---- Building section: Bedrooms (Above/Below Grade), Bathrooms (Total/Partial)
    for i, line in enumerate(lines):
        if line.lower() == "bedrooms" and i + 1 < len(lines) and lines[i + 1].lower() in {"above grade", "below grade"}:
            j = i + 1
            while j + 1 < len(lines) and lines[j].lower() in {"above grade", "below grade"}:
                label, value = lines[j].lower(), lines[j + 1]
                if re.fullmatch(r"\d+", value):
                    if label == "above grade":
                        put(f, "bedrooms", int(value))
                    else:
                        put(f, "bedrooms_extra", int(value))
                j += 2
        if line.lower() == "bathrooms" and i + 1 < len(lines) and lines[i + 1].lower() in {"total", "partial"}:
            j = i + 1
            while j + 1 < len(lines) and lines[j].lower() in {"total", "partial"}:
                label, value = lines[j].lower(), lines[j + 1]
                if re.fullmatch(r"\d+", value):
                    if label == "total":
                        put(f, "bathrooms", int(value))
                    else:
                        out.extras["partial_bathrooms"] = int(value)
                j += 2

    # ---- Property summary
    building = val("Building Type")
    title = val("Title")
    if building:
        out.extras["building_type"] = building
    if title:
        out.extras["title"] = title
    ptype_text = building or ""
    try:
        classified = classify_property_type(ptype_text) if ptype_text else None
    except OutOfScopeProperty as exc:
        out.out_of_scope = str(exc)
        classified = None
    if classified is None and ptype_text and out.out_of_scope is None:
        low, tl = ptype_text.lower(), (title or "").lower()
        if "row" in low or "town" in low:
            if "condo" in tl or "strata" in tl:
                classified = PropertyType.CONDO_TOWNHOUSE
            elif "freehold" in tl:
                classified = PropertyType.FREEHOLD_TOWNHOUSE
            if classified:
                out.notes.append(
                    f"{SITE}: property type '{classified.value}' was inferred from Building Type "
                    f"'{building}' plus Title '{title}'. Please confirm."
                )
            else:
                out.notes.append(
                    f"{SITE}: Building Type '{building}' could not be turned into a property type "
                    f"(Title: '{title or 'not given'}'). Please choose one."
                )
        else:
            out.notes.append(
                f"{SITE}: Building Type '{building}' is not specific enough to pick a property type. "
                "Please choose one."
            )
    put(f, "property_type", classified)

    storeys = val("Storeys")
    if storeys:
        out.extras["storeys"] = storeys
    tax = val("Annual Property Taxes")
    if tax:
        put(f, "listed_annual_property_tax", parse_money(tax.split()[0]))
    nbhd = val("Community Name")
    if nbhd and "neighbourhood" not in f:
        put(f, "neighbourhood", nbhd)

    if "interior_size_min" not in f:
        sq = val("Square Footage")
        rng = parse_size_range(sq) if sq else None
        if rng:
            put(f, "interior_size_min", rng[0])
            put(f, "interior_size_max", rng[1])

    # ---- parking, fees
    ptype_parking = val("Parking Type")
    spaces = val("Total Parking Spaces")
    if ptype_parking:
        put(f, "parking", ptype_parking)
    if spaces and re.fullmatch(r"\d+", spaces):
        put(f, "parking_spaces", int(spaces))
    fee = val("Maintenance Fees")
    if fee:
        if re.search(r"month", fee, re.I):
            put(f, "maintenance_fee", parse_money(fee.split()[0]))
        else:
            out.notes.append(f"{SITE}: maintenance fee '{fee}' is not monthly; enter it by hand.")
    inc = val("Maintenance Fees Include")
    if inc:
        out.extras["maintenance_includes"] = inc

    # ---- features
    put(feat, "flooring", val("Flooring"))
    put(feat, "basement", val("Basement Type"))
    put(feat, "cooling", val("Cooling"))
    put(feat, "heating_type", val("Heating Type"))
    listed_features: list[str] = []
    for label in ("Building Amenities", "Structures", "Pool Type", "Community Features"):
        v = val(label)
        if v:
            listed_features += [p for p in _SPLIT_LIST.split(v) if p]
    seen = set()
    put(feat, "property_features", [x for x in listed_features if not (x.lower() in seen or seen.add(x.lower()))] or None)
    for label, key in (("Appliances Included", "appliances"), ("Exterior Finish", "exterior_finish"),
                       ("Amenities Nearby", "amenities_nearby"), ("Fire Protection", "fire_protection"),
                       ("Maintenance Management Company", "management_company")):
        v = val(label)
        if v:
            out.extras[key] = v
    cross = val("Location Description")
    if cross:
        out.extras["cross_streets"] = cross

    put(f, "description", _description(lines))
    rooms = _rooms(lines)
    if rooms:
        out.extras["rooms"] = rooms

    if not f:
        out.notes.append(f"{SITE}: no recognizable listing facts were found in this text.")
    return out
