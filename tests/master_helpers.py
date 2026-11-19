"""Shared builders for the Phase 4 tests (invented data only)."""
from database.enums import IngestionMethod
from database.models import Property
from database.repository import create_property
from schemas.features import FeaturesInput
from schemas.property import PropertyExtract


def make(**kw) -> PropertyExtract:
    base = dict(
        listing_number="C1000001", listing_status="For Sale", address="12 Maple Street", city="Toronto",
        neighbourhood="Annex", postal_code="M5V 2T6", property_type="Detached", asking_price="$950,000",
        bedrooms=3, bathrooms=2, interior_size_min=1500, interior_size_max=2000, maintenance_fee="0",
        listed_annual_property_tax="5,200", description="Bright home.",
    )
    base.update(kw)
    return PropertyExtract(**base)


def add(session, features: dict | None = None, features_detail: dict | None = None, **kw) -> Property:
    prop = create_property(
        session, make(**kw), ingestion_method=IngestionMethod.MANUAL_ENTRY,
        features=FeaturesInput(**features) if features else None, features_detail=features_detail, verified=True,
    )
    session.commit()
    return prop