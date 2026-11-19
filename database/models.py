"""SQLAlchemy 2.0 models for the MVP schema (Phase 1).

Tables: data_sources, properties, property_features, listing_history,
financial_details, extraction_logs.
Later phases add neighbourhood_research, comparable_properties, property_scores
and user_settings through new Alembic migrations.

Conventions
- UUID primary keys, timestamps stored timezone-aware (UTC).
- Missing information is NULL. Nothing is defaulted to a guessed value.
- Enumerated text values are enforced with CHECK constraints, not PG enum types,
  so values can change through ordinary migrations.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
    Float,
    Boolean
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from database.enums import (
    ExtractionStatus,
    IngestionMethod,
    ListingStatus,
    LogStatus,
    PropertyType,
    TermsStatus,
    Compounding, 
    DownPaymentBasis,
    MortgageType
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite is used only in unit tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")


def _in_list(column: str, values: list[str]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- data_sources
class DataSource(TimestampMixin, Base):
    """A website or origin that listing information comes from, with terms status."""

    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(_in_list("terms_status", TermsStatus.values()), name="terms_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255))
    terms_url: Mapped[str | None] = mapped_column(Text)
    terms_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=TermsStatus.UNREVIEWED.value
    )
    terms_reviewed_on: Mapped[date | None] = mapped_column(Date)
    terms_notes: Mapped[str | None] = mapped_column(Text)

    properties: Mapped[list["Property"]] = relationship(back_populates="source")


# --------------------------------------------------------------------------- properties
class Property(TimestampMixin, Base):
    """One home. Listing facts live here; financial assumptions live in financial_details."""

    __tablename__ = "properties"
    __table_args__ = (
        CheckConstraint(_in_list("property_type", PropertyType.values()), name="property_type"),
        CheckConstraint(_in_list("listing_status", ListingStatus.values()), name="listing_status"),
        CheckConstraint(_in_list("ingestion_method", IngestionMethod.values()), name="ingestion_method"),
        CheckConstraint(_in_list("extraction_status", ExtractionStatus.values()), name="extraction_status"),
        CheckConstraint("asking_price IS NULL OR asking_price >= 0", name="asking_price_nonneg"),
        CheckConstraint(
            "interior_size_min IS NULL OR interior_size_max IS NULL "
            "OR interior_size_min <= interior_size_max",
            name="size_range_order",
        ),
        CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="confidence_range",
        ),
        # Duplicate backstops. The app checks first and offers "update existing";
        # these constraints guarantee no duplicate can slip through.
        Index(
            "uq_properties_listing_number",
            "listing_number",
            unique=True,
            postgresql_where=text("listing_number IS NOT NULL"),
            sqlite_where=text("listing_number IS NOT NULL"),
        ),
        Index(
            "uq_properties_address_key",
            "address_key",
            unique=True,
            postgresql_where=text("address_key IS NOT NULL"),
            sqlite_where=text("address_key IS NOT NULL"),
        ),
        Index("ix_properties_city", "city"),
        Index("ix_properties_neighbourhood", "neighbourhood"),
        Index("ix_properties_property_type", "property_type"),
        Index("ix_properties_asking_price", "asking_price"),
        Index("ix_properties_listing_status", "listing_status"),
        Index("ix_properties_canonical_url", "canonical_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # --- provenance (URL is a reference link only; the app never fetches it)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    ingestion_method: Mapped[str] = mapped_column(String(30), nullable=False)
    listing_url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)

    # --- identity / duplicate detection
    listing_number: Mapped[str | None] = mapped_column(String(50))  # MLS number
    address_key: Mapped[str | None] = mapped_column(String(300))

    # --- listing facts
    listing_status: Mapped[str | None] = mapped_column(String(20))
    listing_date: Mapped[date | None] = mapped_column(Date)  # NULL unless the listing gives it
    date_added_to_app: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    address: Mapped[str | None] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(30))
    city: Mapped[str | None] = mapped_column(String(100))
    neighbourhood: Mapped[str | None] = mapped_column(String(150))
    province: Mapped[str] = mapped_column(String(2), nullable=False, default="ON")
    postal_code: Mapped[str | None] = mapped_column(String(7))  # normalized "A1A 1A1"

    # Phase 6a: property location
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    geocode_source: Mapped[str | None] = mapped_column(Text)
    geocode_confidence: Mapped[str | None] = mapped_column(String(20))

    geocoded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    location_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    location_is_manual: Mapped[bool | None] = mapped_column(
        Boolean
    )

    property_type: Mapped[str | None] = mapped_column(String(30))
    direction: Mapped[str | None] = mapped_column(String(20))  # manual entry

    asking_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    expected_offer_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # manual entry

    bedrooms: Mapped[int | None] = mapped_column(SmallInteger)
    bedrooms_extra: Mapped[int | None] = mapped_column(SmallInteger)  # the "+1" in "3+1"
    bathrooms: Mapped[int | None] = mapped_column(SmallInteger)
    interior_size_min: Mapped[int | None] = mapped_column(Integer)  # square feet
    interior_size_max: Mapped[int | None] = mapped_column(Integer)  # square feet
    year_built: Mapped[int | None] = mapped_column(SmallInteger)
    floor: Mapped[int | None] = mapped_column(SmallInteger)
    parking: Mapped[str | None] = mapped_column(Text)  # as listed
    parking_spaces: Mapped[int | None] = mapped_column(SmallInteger)
    locker: Mapped[str | None] = mapped_column(Text)
    maintenance_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # monthly, as listed
    listed_annual_property_tax: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    listed_tax_year: Mapped[int | None] = mapped_column(SmallInteger)

    description: Mapped[str | None] = mapped_column(Text)  # source text, kept for verification

    # --- data-quality metadata (Property Master columns)
    extraction_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExtractionStatus.PENDING.value
    )
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    data_quality_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    last_verified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_fields: Mapped[list | None] = mapped_column(JsonType)  # list of field names that are NULL
    manual_overrides: Mapped[dict | None] = mapped_column(JsonType)  # {field: true} user-edited fields
    raw_extraction: Mapped[dict | None] = mapped_column(JsonType)  # extracted fields; never full HTML

    source: Mapped[DataSource | None] = relationship(back_populates="properties")
    features: Mapped["PropertyFeatures | None"] = relationship(
        back_populates="property", cascade="all, delete-orphan", uselist=False
    )
    history: Mapped[list["ListingHistory"]] = relationship(
        back_populates="property", cascade="all, delete-orphan", order_by="ListingHistory.observed_at"
    )
    financials: Mapped[list["FinancialDetails"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    logs: Mapped[list["ExtractionLog"]] = relationship(back_populates="property")


# --------------------------------------------------------------------------- property_features
class PropertyFeatures(TimestampMixin, Base):
    """Detailed characteristics, from description extraction or manual entry (1:1 with properties)."""

    __tablename__ = "property_features"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    property_features: Mapped[list | None] = mapped_column(JsonType)  # free list of listed features
    sunlight_rating: Mapped[str | None] = mapped_column(Text)  # manual; e.g. "Good", "Great", "Average", or descriptive text
    living_room_direction: Mapped[str | None] = mapped_column(String(20))  # manual
    bedroom_direction: Mapped[str | None] = mapped_column(String(20))  # manual
    backyard: Mapped[str | None] = mapped_column(String(100))
    backyard_direction: Mapped[str | None] = mapped_column(String(20))  # manual
    backyard_privacy: Mapped[str | None] = mapped_column(String(50))
    balcony: Mapped[str | None] = mapped_column(String(100))
    view: Mapped[str | None] = mapped_column(String(150))
    kitchen_condition: Mapped[str | None] = mapped_column(String(100))
    flooring: Mapped[str | None] = mapped_column(String(150))
    basement: Mapped[str | None] = mapped_column(String(100))
    laundry_type: Mapped[str | None] = mapped_column(String(100))
    heating_type: Mapped[str | None] = mapped_column(String(100))
    cooling: Mapped[str | None] = mapped_column(String(100))
    window_condition: Mapped[str | None] = mapped_column(String(100))
    noise_level: Mapped[str | None] = mapped_column(String(50))  # manual

    # {"kitchen_condition": {"value": "...", "source_type": "description|inference|manual",
    #   "confidence": 0.85}, ...}  Keeps explicit facts separate from inferences.
    ai_detail: Mapped[dict | None] = mapped_column(JsonType)

    property: Mapped[Property] = relationship(back_populates="features")


# --------------------------------------------------------------------------- listing_history
class ListingHistory(Base):
    """Append-only record of observed asking price / status. Never overwritten."""

    __tablename__ = "listing_history"
    __table_args__ = (
        CheckConstraint(
            "listing_status IS NULL OR " + _in_list("listing_status", ListingStatus.values()),
            name="listing_status",
        ),
        CheckConstraint("asking_price IS NULL OR asking_price >= 0", name="asking_price_nonneg"),
        Index("ix_listing_history_property_observed", "property_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    listing_url: Mapped[str | None] = mapped_column(Text)
    asking_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    listing_status: Mapped[str | None] = mapped_column(String(20))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    change_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped[Property] = relationship(back_populates="history")


# --------------------------------------------------------------------------- financial_details
class FinancialDetails(TimestampMixin, Base):
    """Your financial assumptions for a property (one row per named scenario).

    All amounts are CAD. Utility and fee inputs are monthly. Calculated results are
    filled in by the Phase 5 financial service.
    """

    __tablename__ = "financial_details"
    __table_args__ = (
        UniqueConstraint("property_id", "scenario_name", name="property_scenario"),
        CheckConstraint(
            "down_payment_pct IS NULL OR (down_payment_pct >= 0 AND down_payment_pct <= 100)",
            name="down_payment_pct_range",
        ),
        CheckConstraint(
            "mortgage_rate IS NULL OR (mortgage_rate >= 0 AND mortgage_rate <= 100)",
            name="mortgage_rate_range",
        ),
        CheckConstraint("mortgage_type IS NULL OR " + _in_list("mortgage_type", MortgageType.values()),
                        name="mortgage_type_values"),
        CheckConstraint("compounding IS NULL OR " + _in_list("compounding", Compounding.values()),
                        name="compounding_values"),
        CheckConstraint("down_payment_basis IS NULL OR " + _in_list("down_payment_basis", DownPaymentBasis.values()),
                        name="down_payment_basis_values"),
        # At most one primary scenario per property.
        Index("uq_financial_details_one_primary", "property_id", unique=True,
              postgresql_where=text("is_primary"), sqlite_where=text("is_primary")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    scenario_name: Mapped[str] = mapped_column(String(100), nullable=False, default="Default")

    expected_purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    down_payment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    down_payment_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    mortgage_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))  # annual %, e.g. 4.790
    mortgage_type: Mapped[str | None] = mapped_column(String(30))
    amortization_years: Mapped[int | None] = mapped_column(SmallInteger)
    annual_property_tax: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    monthly_maintenance_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    electricity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    natural_gas: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    water: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    home_insurance: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    internet: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    water_heater_rental: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    repair_reserve: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    special_assessment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Phase 5 inputs. NULL means "not entered" (or, for the first two flags, "use the default").
    first_time_buyer: Mapped[bool | None] = mapped_column()          # NULL = config default
    toronto_override: Mapped[bool | None] = mapped_column()          # NULL = decide from the property's city
    term_years: Mapped[int | None] = mapped_column(SmallInteger)
    compounding: Mapped[str | None] = mapped_column(String(20))      # NULL = default for the mortgage type
    non_traditional_down_payment: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    down_payment_basis: Mapped[str | None] = mapped_column(String(10))
    legal_fees: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    title_insurance: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    inspection_costs: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    other_closing_costs: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_primary: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    calculated: Mapped[dict | None] = mapped_column(JsonType)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped[Property] = relationship(back_populates="financials")


# --------------------------------------------------------------------------- extraction_logs
class ExtractionLog(Base):
    """One row per extraction attempt, including rejections. Stores no raw page content."""

    __tablename__ = "extraction_logs"
    __table_args__ = (
        CheckConstraint(_in_list("status", LogStatus.values()), name="status"),
        CheckConstraint(_in_list("ingestion_method", IngestionMethod.values()), name="ingestion_method"),
        Index("ix_extraction_logs_created_at", "created_at"),
        Index("ix_extraction_logs_property_id", "property_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL")
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    ingestion_method: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)  # error or rejection reason
    fields_extracted: Mapped[list | None] = mapped_column(JsonType)
    fields_missing: Mapped[list | None] = mapped_column(JsonType)
    ai_provider: Mapped[str | None] = mapped_column(String(50))
    ai_model: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped[Property | None] = relationship(back_populates="logs")



# ------------------------------------------------------------
# Phase 6a: neighbourhood research
# ------------------------------------------------------------

class NeighbourhoodResearch(Base):
    __tablename__ = "neighbourhood_research"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    params: Mapped[dict] = mapped_column(
        JsonType, nullable=False, default=dict
    )
    provider_status: Mapped[dict | None] = mapped_column(JsonType)


class ResearchItem(Base):
    __tablename__ = "research_items"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "neighbourhood_research.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(120))

    value_num: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6)
    )
    value_text: Mapped[str | None] = mapped_column(Text)
    value_json: Mapped[dict | list | None] = mapped_column(
        JsonType
    )

    unit: Mapped[str | None] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[str | None] = mapped_column(String(20))

    source_name: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text)
    dataset_vintage: Mapped[str | None] = mapped_column(
        String(100)
    )

    retrieved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    observed_from: Mapped[date | None] = mapped_column(Date)
    observed_to: Mapped[date | None] = mapped_column(Date)

    attribution: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class ResearchCache(Base):
    __tablename__ = "research_cache"

    cache_key: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )
    provider: Mapped[str] = mapped_column(
        String(80), nullable=False
    )
    response_json: Mapped[dict | list] = mapped_column(
        JsonType, nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
