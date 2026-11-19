"""Pure functions that clean raw listing text into consistent values.

Rule: when a value cannot be interpreted with confidence, return None.
Never guess.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from database.enums import PropertyType

ONTARIO_POSTAL_RE = re.compile(r"^[KLMNP]\d[ABCEGHJ-NPRSTV-Z]\s?\d[ABCEGHJ-NPRSTV-Z]\d$")


class OutOfScopeProperty(ValueError):
    """Raised for property types the app does not handle (apartment condos)."""


# ----------------------------------------------------------------------------- property type
_APARTMENT_MARKERS = ("condo apt", "apartment", "co-op apt", "coop apt", "co-ownership apt")

# Order matters: more specific phrases first.
_TYPE_RULES: list[tuple[tuple[str, ...], PropertyType]] = [
    (("condo townhouse", "townhouse condo", "condo town", "condo townhome", "condo row"), PropertyType.CONDO_TOWNHOUSE),
    (("semi-detached", "semi detached", "semi-det", "semi det"), PropertyType.SEMI_DETACHED),
    (("link",), PropertyType.LINK),
    (("freehold townhouse", "townhouse freehold", "att/row", "row/townhouse", "attached/row"), PropertyType.FREEHOLD_TOWNHOUSE),
    (("detached", "det."), PropertyType.DETACHED),
]


def classify_property_type(raw: str | None) -> PropertyType | None:
    """Map listing wording to an allowed type.

    Returns None when the wording is unrecognized (user must choose at review).
    Raises OutOfScopeProperty for apartment condos.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip().lower()
    if any(marker in text for marker in _APARTMENT_MARKERS):
        raise OutOfScopeProperty(f"Apartment condos are out of scope: '{raw.strip()}'")
    for needles, ptype in _TYPE_RULES:
        if any(n in text for n in needles):
            return ptype
    return None


# ----------------------------------------------------------------------------- numbers
def parse_money(raw: str | int | float | Decimal | None) -> Decimal | None:
    """'$1,299,000' -> Decimal('1299000'). Returns None if no number is present."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    negative = bool(re.match(r"^\s*[-\u2212(]", raw))  # "-5", "(5)": keep the sign, never drop it
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_size_range(raw: str | None) -> tuple[int, int] | None:
    """Square-foot text to (min, max).

    '1500-2000' -> (1500, 2000); '1,800 sqft' -> (1800, 1800).
    Returns None for text that is not square feet, or has no numbers.
    """
    if not raw:
        return None
    text = raw.lower()
    if re.search(r"\b(m2|m²|sq\.?\s*m|sqm|square\s*met)", text):
        return None  # metric is out of scope; never convert silently
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]
    if not nums or len(nums) > 2:
        return None
    return (min(nums), max(nums))


def parse_bedrooms(raw: str | int | None) -> tuple[int, int | None] | None:
    """'3+1' -> (3, 1); '3' -> (3, None)."""
    if raw is None:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(?:\+\s*(\d+))?\s*", str(raw))
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


# ----------------------------------------------------------------------------- address
def normalize_postal_code(raw: str | None) -> str | None:
    """'m5v2t6' -> 'M5V 2T6'. Returns None if not a valid Ontario postal code."""
    if not raw:
        return None
    text = re.sub(r"\s+", "", raw).upper()
    if not ONTARIO_POSTAL_RE.match(text):
        return None
    return f"{text[:3]} {text[3:]}"


_SUFFIXES = {
    "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "boulevard": "blvd",
    "court": "crt", "crescent": "cres", "lane": "ln", "place": "pl", "terrace": "terr",
    "circle": "cir", "trail": "trl", "square": "sq", "parkway": "pkwy", "highway": "hwy",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def _norm_words(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    words = [_SUFFIXES.get(w, w) for w in text.split()]
    return " ".join(words)


def build_address_key(address: str | None, unit: str | None, postal_code: str | None) -> str | None:
    """Duplicate key = normalized street address + unit + postal code.

    Returns None when address or a valid postal code is missing, because the key
    would not be reliable. (MLS number is then the only duplicate check.)
    """
    postal = normalize_postal_code(postal_code)
    if not address or not address.strip() or not postal:
        return None
    unit_part = _norm_words(unit) if unit else ""
    return f"{_norm_words(address)}|{unit_part}|{postal.replace(' ', '')}"


def canonicalize_url(url: str | None) -> str | None:
    """Strip scheme, 'www.', query, fragment and trailing slash for comparison only."""
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip() if "//" in url else "//" + url.strip())
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    return f"{host}{parts.path.rstrip('/')}"


def domain_of(url: str | None) -> str | None:
    canon = canonicalize_url(url)
    return canon.split("/")[0] if canon else None


# ----------------------------------------------------------------------------- display helpers
_DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def smart_title(text: str | None) -> str | None:
    """Title-case text that is ALL CAPS ('123 EXAMPLE ROAD W' -> '123 Example Road W').

    Text that already has lowercase letters is returned unchanged.
    """
    if not text or text != text.upper() or not any(c.isalpha() for c in text):
        return text
    out = []
    for word in text.split():
        m = re.fullmatch(r"(\d+)(ST|ND|RD|TH)", word)
        if word in _DIRECTIONS:
            out.append(word)
        elif m:
            out.append(m.group(1) + m.group(2).lower())
        else:
            out.append("-".join(p.capitalize() for p in word.split("-")))
    return " ".join(out)


_UNIT_PREFIXED = re.compile(r"^(?:unit|suite|apt\.?|apartment|#)\s*(?P<unit>[A-Za-z0-9]+)\s*-\s*(?P<street>\d.*)$", re.I)
_UNIT_SPACED = re.compile(r"^(?P<unit>[A-Za-z0-9]+)\s+-\s+(?P<street>\d.*)$")


def split_unit_address(full: str | None) -> tuple[str | None, str | None]:
    """'Unit 7 - 123 Example Road W' or '7 - 123 EXAMPLE ROAD W' -> ('7', '123 Example Road W').

    A street-number range such as '12-14 Main St' (no spaces around the hyphen,
    no 'Unit' prefix) is NOT treated as a unit.
    """
    if not full or not full.strip():
        return None, None
    text = full.strip()
    m = _UNIT_PREFIXED.match(text) or _UNIT_SPACED.match(text)
    if m:
        return m.group("unit"), smart_title(m.group("street").strip())
    return None, smart_title(text)
