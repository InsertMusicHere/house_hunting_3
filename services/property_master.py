"""Property Master: turns saved properties into a table, with filtering and sorting.

No page code and no SQL here. repository.list_properties() supplies the Property objects.
Everything shown in the table is derived from stored values; the only calculated columns
are price per sq ft and the data-quality percentage, and neither is stored by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from database.models import Property
from utils.quality import percent, quality_for_property
from services.financial_service import result_amount

try:  # Windows needs the 'tzdata' package for zoneinfo (see requirements.txt)
    from zoneinfo import ZoneInfo

    _TORONTO = ZoneInfo("America/Toronto")
except Exception:  # noqa: BLE001  (fall back to UTC rather than failing the page)
    _TORONTO = timezone.utc


# --------------------------------------------------------------------------- columns
@dataclass(frozen=True)
class Col:
    key: str
    label: str
    kind: str  # text | int | money | pct | datetime | link
    default: bool = False
    in_table: bool = True  # False: only offered in the export (description)
    help: str | None = None


COLUMNS: list[Col] = [
    Col("address", "Address", "text", True),
    Col("unit", "Unit", "text", True),
    Col("city", "City", "text", True),
    Col("neighbourhood", "Neighbourhood", "text", True),
    Col("postal_code", "Postal code", "text"),
    Col("property_type", "Property type", "text", True),
    Col("listing_status", "Listing status", "text", True),
    Col("listing_number", "MLS number", "text"),
    Col("listing_url", "Listing link", "link","text", True),
    Col("asking_price", "Asking price", "money", True),
    Col("direction", "Direction", "text", True),
    Col("sunlight_rating", "Sunlight rating", "text", True),
    Col("expected_offer_price", "Expected offer price", "money"),
    Col("bedrooms", "Bedrooms", "text", True, help="Shown as listed, e.g. 3+1."),
    Col("bathrooms", "Bathrooms", "int", True),
    Col("interior_size_min", "Size min (sq ft)", "int", True),
    Col("interior_size_max", "Size max (sq ft)", "int", True),
    Col("price_per_sqft", "Price / sq ft", "money", True,
        help="Asking price divided by interior size. When the listing gives a size range, "
             "the midpoint is used. Blank when size or price is missing. Not stored."),
    Col("year_built", "Year built", "int"),
    Col("floor", "Floor", "int"),
    Col("parking", "Parking", "text"),
    Col("parking_spaces", "Parking spaces", "int"),
    Col("locker", "Locker", "text"),
    Col("maintenance_fee", "Maintenance fee (monthly)", "money", True),
    Col("listed_annual_property_tax", "Annual property tax", "money", True),
    Col("listed_tax_year", "Tax year", "int"),
    Col("property_features", "Listed features", "text"),
    Col("living_room_direction", "Living room direction", "text"),
    Col("bedroom_direction", "Bedroom direction", "text"),
    Col("backyard", "Backyard", "text", True),
    Col("backyard_direction", "Backyard direction", "text"),
    Col("backyard_privacy", "Backyard privacy", "text"),
    Col("balcony", "Balcony", "text"),
    Col("view", "View", "text"),
    Col("kitchen_condition", "Kitchen condition", "text"),
    Col("flooring", "Flooring", "text"),
    Col("basement", "Basement", "text"),
    Col("laundry_type", "Laundry type", "text"),
    Col("heating_type", "Heating type", "text"),
    Col("cooling", "Cooling / AC", "text"),
    Col("window_condition", "Window condition", "text"),
    Col("noise_level", "Noise level", "text"),
    Col("monthly_outgoing", "Monthly outgoing", "money", True,
        help="Total monthly cost of owning, from this property's primary financial scenario. "
             "Blank when there is no primary scenario or it could not be calculated."),
    Col("cash_required", "Cash required", "money", True,
        help="Down payment plus closing costs (and any special assessment), from the primary financial scenario."),
    Col("data_quality", "Data quality %", "pct",
        True, help="Share of the 15 tracked listing fields that are filled in."),
    Col("missing_fields", "Missing fields", "text"),
    Col("data_source", "Data source", "text"),
    Col("date_added", "Date added", "datetime", True),
    Col("updated_at", "Last updated", "datetime"),
    Col("extraction_status", "Extraction status", "text", True),
    Col("extraction_confidence", "Extraction confidence", "text"),
    Col("last_verified", "Last verified", "datetime", True),
    Col("manual_overrides", "Manual overrides", "text",
        help="Fields you typed or changed by hand. Later listing updates never overwrite these."),
    Col("description", "Description", "text", in_table=False),
]
BY_KEY = {c.key: c for c in COLUMNS}
TABLE_COLUMNS = [c for c in COLUMNS if c.in_table]
DEFAULT_COLUMNS = [c.key for c in TABLE_COLUMNS if c.default]

FEATURE_KEYS = [
    "sunlight_rating", "living_room_direction", "bedroom_direction", "backyard",
    "backyard_direction", "backyard_privacy", "balcony", "view", "kitchen_condition",
    "flooring", "basement", "laundry_type", "heating_type", "cooling", "window_condition",
    "noise_level",
]

# Read-only details view, grouped.
DETAIL_SECTIONS: dict[str, list[str]] = {
    "Listing": [
        "address", "unit", "city", "neighbourhood", "postal_code", "property_type", "listing_status",
        "listing_number", "listing_url", "asking_price", "maintenance_fee", "listed_annual_property_tax",
        "listed_tax_year", "bedrooms", "bathrooms", "interior_size_min", "interior_size_max",
        "price_per_sqft", "year_built", "floor", "parking", "parking_spaces", "locker",
    ],
    "Your entries": [
        "direction", "expected_offer_price", "sunlight_rating", "living_room_direction",
        "bedroom_direction", "backyard_direction", "noise_level",
    ],
    "Features": [
        "property_features", "backyard", "backyard_privacy", "balcony", "view", "kitchen_condition",
        "flooring", "basement", "laundry_type", "heating_type", "cooling", "window_condition",
    ],
    "Data quality": [
        "data_quality", "missing_fields", "manual_overrides", "data_source", "extraction_status",
        "extraction_confidence", "last_verified", "updated_at", "date_added",
    ],
}


# --------------------------------------------------------------------------- helpers
def _pretty(key: str) -> str:
    return key.replace("_", " ").capitalize()


def to_local(dt: datetime | None) -> datetime | None:
    """UTC (or naive, treated as UTC) -> America/Toronto wall time, without tzinfo."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TORONTO).replace(tzinfo=None)


def price_per_sqft(price: Any, size_min: int | None, size_max: int | None) -> float | None:
    """Asking price / size; the midpoint when a range is given. None if either is missing."""
    if price is None:
        return None
    sizes = [s for s in (size_min, size_max) if s]
    if not sizes:
        return None
    return round(float(price) / (sum(sizes) / len(sizes)), 2)


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def data_source_text(prop: Property) -> str:
    names: list[str] = []
    for s in (prop.raw_extraction or {}).get("sources", []):
        name = s.get("source")
        if name and name not in names:
            names.append(name)
    if not names and prop.source is not None:
        names = [prop.source.name]
    return ", ".join(names) if names else "Manual entry"


def manual_override_labels(prop: Property) -> list[str]:
    keys = [k for k, v in (prop.manual_overrides or {}).items() if v]
    detail = prop.features.ai_detail if prop.features is not None else None
    keys += [k for k, v in (detail or {}).items() if isinstance(v, dict) and v.get("source_type") == "manual"]
    seen: list[str] = []
    for k in keys:
        if k not in seen:
            seen.append(k)
    return [_pretty(k) for k in seen]


def property_row(prop: Property) -> dict[str, Any]:
    """One table row. Keys starting with '_' are helpers for filtering and are never displayed."""
    feats = prop.features
    score, missing = quality_for_property(prop)
    bedrooms = None
    if prop.bedrooms is not None:
        bedrooms = f"{prop.bedrooms}+{prop.bedrooms_extra}" if prop.bedrooms_extra else str(prop.bedrooms)
    elif prop.bedrooms_extra:
        bedrooms = f"0+{prop.bedrooms_extra}"
    total_bedrooms = None if prop.bedrooms is None else prop.bedrooms + (prop.bedrooms_extra or 0)

    primary = next((f for f in prop.financials if f.is_primary), None)
    calc = primary.calculated if primary is not None else None

    row: dict[str, Any] = {
        "_id": str(prop.id),
        "_bedrooms_total": total_bedrooms,
        "_search": " ".join(
            str(x).lower() for x in (
                prop.address, prop.unit, prop.city, prop.neighbourhood, prop.postal_code, prop.listing_number,
            ) if x
        ),
        "address": prop.address, "unit": prop.unit, "city": prop.city, "neighbourhood": prop.neighbourhood,
        "postal_code": prop.postal_code, "property_type": prop.property_type,
        "listing_status": prop.listing_status, "listing_number": prop.listing_number,
        "listing_url": prop.listing_url,
        "asking_price": _num(prop.asking_price), "expected_offer_price": _num(prop.expected_offer_price),
        "bedrooms": bedrooms, "bathrooms": prop.bathrooms,
        "interior_size_min": prop.interior_size_min, "interior_size_max": prop.interior_size_max,
        "price_per_sqft": price_per_sqft(prop.asking_price, prop.interior_size_min, prop.interior_size_max),
        "year_built": prop.year_built, "floor": prop.floor, "parking": prop.parking,
        "parking_spaces": prop.parking_spaces, "locker": prop.locker,
        "maintenance_fee": _num(prop.maintenance_fee),
        "listed_annual_property_tax": _num(prop.listed_annual_property_tax),
        "listed_tax_year": prop.listed_tax_year, "direction": prop.direction,
        "property_features": ", ".join(feats.property_features) if feats and feats.property_features else None,
        "monthly_outgoing": _num(result_amount(calc, "monthly", "total")),
        "cash_required": _num(result_amount(calc, "cash", "total_cash_required")),
        "data_quality": percent(score),
        "missing_fields": ", ".join(_pretty(m) for m in missing) or None,
        "data_source": data_source_text(prop),
        "date_added": to_local(prop.date_added_to_app), "updated_at": to_local(prop.updated_at),
        "extraction_status": prop.extraction_status,
        "extraction_confidence": None if prop.extraction_confidence is None else str(prop.extraction_confidence),
        "last_verified": to_local(prop.last_verified),
        "manual_overrides": ", ".join(manual_override_labels(prop)) or None,
        "description": prop.description,
    }
    for key in FEATURE_KEYS:
        row[key] = getattr(feats, key, None) if feats is not None else None
    return row


def build_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Rows -> DataFrame with proper numeric and datetime dtypes (blanks stay blank)."""
    df = pd.DataFrame(rows, columns=["_id", "_bedrooms_total", "_search", *[c.key for c in COLUMNS]])
    for col in COLUMNS:
        if col.kind in ("int", "money", "pct"):
            df[col.key] = pd.to_numeric(df[col.key], errors="coerce")
        elif col.kind == "datetime":
            df[col.key] = pd.to_datetime(df[col.key], errors="coerce")
    df["_bedrooms_total"] = pd.to_numeric(df["_bedrooms_total"], errors="coerce")
    return df.reset_index(drop=True)


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def format_value(kind: str, value: Any) -> str:
    """Human text for the read-only details view. Blank values show as an em dash."""
    if is_blank(value):
        return "—"
    if kind == "money":
        v = float(value)
        return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"
    if kind == "pct":
        return f"{int(value)}%"
    if kind == "int":
        return str(int(value))
    if kind == "datetime":
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def detail_pairs(row: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Section title -> [(label, text)] for the details view."""
    return {
        title: [(BY_KEY[k].label, format_value(BY_KEY[k].kind, row.get(k))) for k in keys]
        for title, keys in DETAIL_SECTIONS.items()
    }


# --------------------------------------------------------------------------- filtering and sorting
@dataclass
class Filters:
    text: str = ""
    cities: list[str] = field(default_factory=list)
    neighbourhoods: list[str] = field(default_factory=list)
    property_types: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    extraction_statuses: list[str] = field(default_factory=list)
    price_min: float | None = None
    price_max: float | None = None
    fee_min: float | None = None
    fee_max: float | None = None
    bedrooms_min: int | None = None  # compares total bedrooms (3+1 counts as 4)
    bathrooms_min: int | None = None
    only_missing: bool = False

    def numeric_active(self) -> bool:
        """True when a numeric filter is on; rows with a blank value are then excluded."""
        return any(v is not None for v in (
            self.price_min, self.price_max, self.fee_min, self.fee_max, self.bedrooms_min, self.bathrooms_min,
        ))


def _in(series: pd.Series, chosen: list[str]) -> pd.Series:
    return series.isin(chosen).fillna(False).astype(bool)


def _at_least(series: pd.Series, value: float) -> pd.Series:
    return (series >= value).fillna(False).astype(bool)


def _at_most(series: pd.Series, value: float) -> pd.Series:
    return (series <= value).fillna(False).astype(bool)


def apply_filters(df: pd.DataFrame, f: Filters) -> pd.DataFrame:
    """Rows are kept only if they satisfy every active filter. A blank value never passes a
    numeric filter (the app does not guess what a missing price or fee might have been)."""
    mask = pd.Series(True, index=df.index)
    for term in f.text.lower().split():
        mask &= df["_search"].str.contains(term, regex=False).fillna(False).astype(bool)
    if f.cities:
        mask &= _in(df["city"], f.cities)
    if f.neighbourhoods:
        mask &= _in(df["neighbourhood"], f.neighbourhoods)
    if f.property_types:
        mask &= _in(df["property_type"], f.property_types)
    if f.statuses:
        mask &= _in(df["listing_status"], f.statuses)
    if f.extraction_statuses:
        mask &= _in(df["extraction_status"], f.extraction_statuses)
    if f.price_min is not None:
        mask &= _at_least(df["asking_price"], f.price_min)
    if f.price_max is not None:
        mask &= _at_most(df["asking_price"], f.price_max)
    if f.fee_min is not None:
        mask &= _at_least(df["maintenance_fee"], f.fee_min)
    if f.fee_max is not None:
        mask &= _at_most(df["maintenance_fee"], f.fee_max)
    if f.bedrooms_min is not None:
        mask &= _at_least(df["_bedrooms_total"], f.bedrooms_min)
    if f.bathrooms_min is not None:
        mask &= _at_least(df["bathrooms"], f.bathrooms_min)
    if f.only_missing:
        mask &= df["missing_fields"].notna()
    return df[mask].reset_index(drop=True)


SORTABLE = [c.key for c in TABLE_COLUMNS]


def sort_frame(df: pd.DataFrame, key: str, ascending: bool) -> pd.DataFrame:
    """Sort by one column. Blank values always go last. Text sorts without regard to case."""
    if key not in SORTABLE:
        raise ValueError(f"Cannot sort by {key!r}")
    kind = BY_KEY[key].kind
    sort_key = (lambda s: s.str.lower()) if kind in ("text", "link") else None
    out = df.sort_values(key, ascending=ascending, na_position="last", key=sort_key, kind="stable")
    return out.reset_index(drop=True)


def distinct(df: pd.DataFrame, column: str) -> list[str]:
    return sorted({v for v in df[column].dropna().astype(str) if v.strip()}, key=str.lower)