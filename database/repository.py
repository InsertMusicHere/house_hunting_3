"""Database access for properties. All SQL lives here, not in pages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from utils.quality import quality_for_property

from database.enums import ExtractionStatus, IngestionMethod
from database.models import DataSource, FinancialDetails, ListingHistory, Property, PropertyFeatures
from schemas.features import FeaturesInput
from schemas.property import PropertyExtract
from utils.normalization import build_address_key, canonicalize_url, domain_of


@dataclass(frozen=True)
class DuplicateMatch:
    property: Property
    reasons: tuple[str, ...]  # any of: "listing_number", "address", "url"


def find_duplicates(
    session: Session,
    *,
    listing_number: str | None,
    address: str | None,
    unit: str | None,
    postal_code: str | None,
    listing_url: str | None,
) -> list[DuplicateMatch]:
    """Return existing properties that match by MLS number, address key, or URL.

    The caller (Add Properties page) shows these and asks whether to update the
    existing record and add a history row.
    """
    key = build_address_key(address, unit, postal_code)
    canon = canonicalize_url(listing_url)

    conditions = []
    if listing_number:
        conditions.append(Property.listing_number == listing_number)
    if key:
        conditions.append(Property.address_key == key)
    if canon:
        conditions.append(Property.canonical_url == canon)
    if not conditions:
        return []

    matches: list[DuplicateMatch] = []
    for prop in session.scalars(select(Property).where(or_(*conditions))):
        reasons = []
        if listing_number and prop.listing_number == listing_number:
            reasons.append("listing_number")
        if key and prop.address_key == key:
            reasons.append("address")
        if canon and prop.canonical_url == canon:
            reasons.append("url")
        matches.append(DuplicateMatch(prop, tuple(reasons)))
    return matches


def _source_for(session: Session, url: str | None) -> DataSource | None:
    dom = domain_of(url)
    if not dom:
        return None
    return session.scalar(select(DataSource).where(DataSource.domain == dom))

def refresh_quality(prop: Property) -> None:
    """Recompute missing_fields and data_quality_score from the property's current values."""
    score, missing = quality_for_property(prop)
    prop.data_quality_score = score
    prop.missing_fields = missing

def create_property(
    session: Session,
    data: PropertyExtract,
    *,
    ingestion_method: IngestionMethod,
    raw_extraction: dict | None = None,
    extraction_confidence: Decimal | None = None,
    features: FeaturesInput | None = None,
    features_detail: dict | None = None,
    manual_overrides: dict | None = None,
    history_rows: list[dict] | None = None,
    verified: bool = False,
) -> Property:
    """Insert a property, its features, imported history rows, and an 'Initial entry' history row."""
    prop = Property(
        source=_source_for(session, data.listing_url),
        ingestion_method=ingestion_method.value,
        listing_url=data.listing_url,
        canonical_url=data.canonical_url,
        listing_number=data.listing_number,
        address_key=build_address_key(data.address, data.unit, data.postal_code),
        listing_status=data.listing_status.value if data.listing_status else None,
        listing_date=data.listing_date,
        address=data.address,
        unit=data.unit,
        city=data.city,
        neighbourhood=data.neighbourhood,
        postal_code=data.postal_code,
        property_type=data.property_type.value if data.property_type else None,
        asking_price=data.asking_price,
        bedrooms=data.bedrooms,
        bedrooms_extra=data.bedrooms_extra,
        bathrooms=data.bathrooms,
        interior_size_min=data.interior_size_min,
        interior_size_max=data.interior_size_max,
        year_built=data.year_built,
        floor=data.floor,
        parking=data.parking,
        parking_spaces=data.parking_spaces,
        locker=data.locker,
        maintenance_fee=data.maintenance_fee,
        listed_annual_property_tax=data.listed_annual_property_tax,
        listed_tax_year=data.listed_tax_year,
        description=data.description,
        direction=data.direction,
        expected_offer_price=data.expected_offer_price,
        extraction_status=(ExtractionStatus.VERIFIED if verified else ExtractionStatus.EXTRACTED).value,
        last_verified=datetime.now(timezone.utc) if verified else None,
        extraction_confidence=extraction_confidence,
        missing_fields=data.missing_fields(),
        manual_overrides=manual_overrides or {},
        raw_extraction=raw_extraction,
    )
    refresh_quality(prop)
    _add_imported_history(prop, history_rows)
    prop.history.append(
        ListingHistory(
            listing_url=data.listing_url,
            asking_price=data.asking_price,
            listing_status=prop.listing_status,
            change_note="Initial entry",
        )
    )
    if features is not None and (features.provided() or features_detail):
        prop.features = PropertyFeatures(**features.provided(), ai_detail=features_detail or None)
    session.add(prop)
    session.flush()
    return prop


def _add_imported_history(prop: Property, rows: list[dict] | None) -> None:
    """Append past observations (e.g. a site's listing-history table). Existing rows are never changed."""
    existing = {(h.change_note, h.observed_at.date() if h.observed_at else None) for h in prop.history}
    for row in rows or []:
        observed = row.get("observed_on")
        if isinstance(observed, date) and not isinstance(observed, datetime):
            observed = datetime(observed.year, observed.month, observed.day, tzinfo=timezone.utc)
        key = (row.get("note"), observed.date() if observed else None)
        if key in existing:
            continue  # already imported earlier
        existing.add(key)
        status = row.get("listing_status")
        prop.history.append(
            ListingHistory(
                listing_url=prop.listing_url,
                asking_price=row.get("asking_price"),
                listing_status=getattr(status, "value", status),
                observed_at=observed or datetime.now(timezone.utc),
                change_note=row.get("note"),
            )
        )


def fill_missing_features(
    session: Session, prop: Property, features: FeaturesInput | None, detail: dict | None = None
) -> None:
    """Add feature values to an existing property WITHOUT overwriting anything already stored."""
    if features is None or not (features.provided() or detail):
        return
    if prop.features is None:
        prop.features = PropertyFeatures(**features.provided(), ai_detail=detail or None)
    else:
        # Fields you set or cleared by hand (source_type "manual") are never refilled.
        manual = {
            k for k, v in (prop.features.ai_detail or {}).items()
            if isinstance(v, dict) and v.get("source_type") == "manual"
        }
        for key, value in features.provided().items():
            if key not in manual and getattr(prop.features, key) is None:
                setattr(prop.features, key, value)
        merged = dict(prop.features.ai_detail or {})
        for key, info in (detail or {}).items():
            merged.setdefault(key, info)
        prop.features.ai_detail = merged or None
    session.flush()


def update_property_from_new_listing(
    session: Session,
    prop: Property,
    data: PropertyExtract,
    *,
    note: str = "Updated from new listing data",
    history_rows: list[dict] | None = None,
) -> Property:
    """Apply newer listing data to an existing property and append a history row.

    Only non-NULL incoming values overwrite; user-edited fields (manual_overrides)
    are left untouched. History is appended, never rewritten.
    """
    protected = set((prop.manual_overrides or {}).keys())

    old_location_identity = (
        prop.address,
        prop.unit,
        prop.city,
        prop.postal_code,
    )

    updatable = [
        "listing_status", "listing_date", "asking_price", "bedrooms", "bedrooms_extra", "bathrooms",
        "interior_size_min", "interior_size_max", "year_built", "floor", "parking", "parking_spaces",
        "locker", "maintenance_fee", "listed_annual_property_tax", "listed_tax_year", "description",
        "neighbourhood", "city",
    ]
    for field in updatable:
        if field in protected:
            continue
        value = getattr(data, field)
        if value is None:
            continue
        if field == "listing_status":
            value = value.value
        setattr(prop, field, value)
    if data.listing_url:
        prop.listing_url = data.listing_url
        prop.canonical_url = data.canonical_url
    if data.listing_number and not prop.listing_number:
        prop.listing_number = data.listing_number
    
    new_location_identity = (
        prop.address,
        prop.unit,
        prop.city,
        prop.postal_code,
    )

    if old_location_identity != new_location_identity:
        prop.location_verified_at = None

    refresh_quality(prop)
    _add_imported_history(prop, history_rows)
    prop.history.append(
        ListingHistory(
            listing_url=prop.listing_url,
            asking_price=prop.asking_price,
            listing_status=prop.listing_status,
            change_note=note,
        )
    )
    session.flush()
    return prop

# --------------------------------------------------------------------------- Phase 4: Property Master
def list_properties(session: Session) -> list[Property]:
    """All saved properties, newest first, with features and source loaded."""
    stmt = (
        select(Property)
        .options(
            selectinload(Property.features), selectinload(Property.source), selectinload(Property.financials)
        )
        .order_by(Property.date_added_to_app.desc())
    )
    return list(session.scalars(stmt))


def get_property(session: Session, property_id) -> Property | None:
    return session.get(Property, property_id)


def history_for_properties(session: Session, property_ids: list) -> list[ListingHistory]:
    """Listing-history rows for the given properties, oldest first within each property."""
    if not property_ids:
        return []
    stmt = (
        select(ListingHistory)
        .where(ListingHistory.property_id.in_(property_ids))
        .order_by(ListingHistory.property_id, ListingHistory.observed_at)
    )
    return list(session.scalars(stmt))


def update_property_manually(
    session: Session, prop: Property, data: PropertyExtract, feats: FeaturesInput, *,
    property_fields: list[str], feature_fields: list[str],
    overrides: dict[str, bool], feature_changes: dict[str, str],
) -> Property:
    old_location_identity = (
        prop.address,
        prop.unit,
        prop.city,
        prop.postal_code,
    )
    """Write an owner edit. Blank values are written as NULL (clearing a field is allowed)."""
    for name in property_fields:
        value = getattr(data, name)
        if name in ("listing_status", "property_type") and value is not None:
            value = value.value
        setattr(prop, name, value)
    
    new_location_identity = (
        prop.address,
        prop.unit,
        prop.city,
        prop.postal_code,
    )

    if old_location_identity != new_location_identity:
        prop.location_verified_at = None

    prop.listing_url = data.listing_url
    prop.canonical_url = data.canonical_url
    prop.address_key = build_address_key(data.address, data.unit, data.postal_code)
    prop.source = _source_for(session, data.listing_url) or prop.source
    prop.manual_overrides = {**(prop.manual_overrides or {}), **overrides}  # new dict: JSON change tracking

    provided = feats.model_dump()
    if prop.features is None and (feats.provided() or feature_changes):
        prop.features = PropertyFeatures()
    if prop.features is not None:
        for name in feature_fields:
            setattr(prop.features, name, provided.get(name))
        detail = dict(prop.features.ai_detail or {})
        for name, text in feature_changes.items():
            detail[name] = {"value": text or None, "source_type": "manual"}
        prop.features.ai_detail = detail or None

    prop.extraction_status = ExtractionStatus.VERIFIED.value
    prop.last_verified = datetime.now(timezone.utc)  # you just reviewed it
    refresh_quality(prop)
    session.flush()
    return prop


def delete_property(session: Session, prop: Property) -> dict[str, int]:
    """Hard-delete one property. Features, history and financial rows go with it;
    extraction logs are kept with their property link cleared."""
    counts = {
        "history_rows": len(prop.history),
        "financial_scenarios": len(prop.financials),
        "logs_kept": len(prop.logs),
    }
    session.delete(prop)
    session.flush()
    return counts


def recalculate_all_quality(session: Session) -> int:
    """Backfill data_quality_score / missing_fields for every property. Returns how many."""
    props = list(session.scalars(select(Property)))
    for prop in props:
        refresh_quality(prop)
    session.flush()
    return len(props)

def scenarios_for_property(session: Session, property_id) -> list[FinancialDetails]:
    """A property's scenarios: the primary one first, then by name."""
    stmt = (
        select(FinancialDetails)
        .where(FinancialDetails.property_id == property_id)
        .order_by(FinancialDetails.is_primary.desc(), FinancialDetails.scenario_name)
    )
    return list(session.scalars(stmt))


def get_scenario(session: Session, scenario_id) -> FinancialDetails | None:
    return session.get(FinancialDetails, scenario_id)


def all_scenarios(session: Session) -> list[FinancialDetails]:
    return list(session.scalars(select(FinancialDetails)))