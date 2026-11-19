"""Pydantic model for one extracted/entered property (validation layer).

Everything is optional because the app never invents missing values:
a missing field stays None and is listed in missing_fields.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from database.enums import ListingStatus, PropertyType
from utils.normalization import (
    canonicalize_url,
    classify_property_type,
    normalize_postal_code,
    parse_money,
)

# Fields counted when reporting what is missing from a listing.
TRACKED_FIELDS = [
    "listing_number", "listing_status", "address", "city", "neighbourhood", "postal_code",
    "property_type", "asking_price", "bedrooms", "bathrooms", "interior_size_min",
    "interior_size_max", "year_built", "parking", "description",
]


class PropertyExtract(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    listing_url: str | None = None
    listing_number: str | None = None
    listing_status: ListingStatus | None = None
    listing_date: date | None = None
    address: str | None = None
    unit: str | None = None
    city: str | None = None
    neighbourhood: str | None = None
    postal_code: str | None = None
    property_type: PropertyType | None = None
    asking_price: Decimal | None = None
    bedrooms: int | None = None
    bedrooms_extra: int | None = None
    bathrooms: int | None = None
    interior_size_min: int | None = None
    interior_size_max: int | None = None
    year_built: int | None = None
    floor: int | None = None
    parking: str | None = None
    parking_spaces: int | None = None
    locker: str | None = None
    maintenance_fee: Decimal | None = None
    listed_annual_property_tax: Decimal | None = None
    listed_tax_year: int | None = None
    description: str | None = None
    direction: str | None = None                 # manual entry
    expected_offer_price: Decimal | None = None  # manual entry

    # ---- field validators ------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data):
        """Runs before any field validator: empty strings mean 'not provided'."""
        if isinstance(data, dict):
            return {k: (None if isinstance(v, str) and not v.strip() else v) for k, v in data.items()}
        return data

    @field_validator("property_type", mode="before")
    @classmethod
    def _property_type(cls, v):
        if v is None or isinstance(v, PropertyType):
            return v
        # Raises OutOfScopeProperty (a ValueError) for apartments; None if unrecognized.
        return classify_property_type(str(v))

    @field_validator("postal_code", mode="before")
    @classmethod
    def _postal(cls, v):
        if v is None:
            return None
        norm = normalize_postal_code(str(v))
        if norm is None:
            raise ValueError(f"'{v}' is not a valid Ontario postal code")
        return norm

    @field_validator("asking_price", "maintenance_fee", "listed_annual_property_tax", "expected_offer_price", mode="before")
    @classmethod
    def _money(cls, v):
        if v is None:
            return None
        parsed = parse_money(v)
        if parsed is None:
            raise ValueError(f"'{v}' is not a valid amount")
        return parsed

    @field_validator("asking_price", "maintenance_fee", "listed_annual_property_tax", "expected_offer_price")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("must not be negative")
        return v

    @field_validator("bedrooms", "bedrooms_extra", "bathrooms", "parking_spaces")
    @classmethod
    def _small_counts(cls, v):
        if v is not None and not (0 <= v <= 30):
            raise ValueError("must be between 0 and 30")
        return v

    @field_validator("year_built")
    @classmethod
    def _year(cls, v):
        if v is not None and not (1750 <= v <= date.today().year + 3):
            raise ValueError("year_built is outside a plausible range")
        return v

    @model_validator(mode="after")
    def _size_order(self):
        lo, hi = self.interior_size_min, self.interior_size_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("interior_size_min must not exceed interior_size_max")
        return self

    # ---- helpers -----------------------------------------------------------
    @property
    def canonical_url(self) -> str | None:
        return canonicalize_url(self.listing_url)

    def missing_fields(self) -> list[str]:
        return [f for f in TRACKED_FIELDS if getattr(self, f) is None]
