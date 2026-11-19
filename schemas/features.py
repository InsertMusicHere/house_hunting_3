"""Validation for the detailed-characteristics fields (property_features table)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

TEXT_FIELDS = [
    "sunlight_rating", "living_room_direction", "bedroom_direction", "backyard",
    "backyard_direction", "backyard_privacy", "balcony", "view", "kitchen_condition",
    "flooring", "basement", "laundry_type", "heating_type", "cooling", "window_condition",
    "noise_level",
]

# Fields the user fills in by hand; they can never be extracted from a listing.
MANUAL_ONLY_FIELDS = [
    "sunlight_rating", "living_room_direction", "bedroom_direction", "backyard_direction",
    "noise_level",
]


class FeaturesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    property_features: list[str] | None = None
    sunlight_rating: str | None = None
    living_room_direction: str | None = None
    bedroom_direction: str | None = None
    backyard: str | None = None
    backyard_direction: str | None = None
    backyard_privacy: str | None = None
    balcony: str | None = None
    view: str | None = None
    kitchen_condition: str | None = None
    flooring: str | None = None
    basement: str | None = None
    laundry_type: str | None = None
    heating_type: str | None = None
    cooling: str | None = None
    window_condition: str | None = None
    noise_level: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data):
        if isinstance(data, dict):
            return {k: (None if isinstance(v, str) and not v.strip() else v) for k, v in data.items()}
        return data

    @field_validator("property_features", mode="before")
    @classmethod
    def _split_list(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v = [p.strip() for p in v.split(",")]
        cleaned = [p.strip() for p in v if p and p.strip()]
        return cleaned or None

    def provided(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}
