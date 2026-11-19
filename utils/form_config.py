"""Labels, layouts and helpers shared by the Add Property and Property Master forms.

Moved out of pages/1_Add_Property.py so both pages use one definition.
"""
from __future__ import annotations

from pydantic import ValidationError

from database.enums import ListingStatus, PropertyType

LABELS = {
    "listing_number": "MLS / listing number", "listing_status": "Listing status",
    "listing_date": "Listing date (YYYY-MM-DD)", "address": "Street address *", "unit": "Unit",
    "city": "City *", "neighbourhood": "Neighbourhood", "postal_code": "Postal code",
    "property_type": "Property type", "asking_price": "Asking price (CAD)", "bedrooms": "Bedrooms",
    "bedrooms_extra": "Extra bedrooms (the +1)", "bathrooms": "Bathrooms",
    "interior_size_min": "Interior size min (sq ft)", "interior_size_max": "Interior size max (sq ft)",
    "year_built": "Year built", "floor": "Floor", "parking": "Parking (as listed)",
    "parking_spaces": "Parking spaces", "locker": "Locker",
    "maintenance_fee": "Maintenance fee (monthly, CAD)",
    "listed_annual_property_tax": "Annual property tax (as listed)", "listed_tax_year": "Tax year",
    "direction": "Direction (stepping out)", "expected_offer_price": "Expected offer price (your entry)",
    "property_features": "Listed features (comma separated)", "sunlight_rating": "Sunlight rating (your entry)",
    "living_room_direction": "Living room direction (your entry)",
    "bedroom_direction": "Bedroom direction (your entry)", "backyard": "Backyard",
    "backyard_direction": "Backyard direction (your entry)", "backyard_privacy": "Backyard privacy",
    "balcony": "Balcony", "view": "View", "kitchen_condition": "Kitchen condition",
    "flooring": "Flooring", "basement": "Basement", "laundry_type": "Laundry type",
    "heating_type": "Heating type", "cooling": "Cooling", "window_condition": "Window condition",
    "noise_level": "Noise level (your entry)",
    "description": "Description", "listing_url": "Reference link",
}
FACT_LAYOUT = [
    ["address", "unit", "city", "neighbourhood"],
    ["postal_code", "property_type", "listing_status", "listing_date"],
    ["listing_number", "asking_price", "maintenance_fee", "listed_annual_property_tax"],
    ["listed_tax_year", "bedrooms", "bedrooms_extra", "bathrooms"],
    ["interior_size_min", "interior_size_max", "year_built", "floor"],
    ["parking", "parking_spaces", "locker"],
]
MY_LAYOUT = [["direction", "expected_offer_price"]]
FEATURE_LAYOUT = [
    ["flooring", "basement", "heating_type", "cooling"],
    ["laundry_type", "window_condition", "kitchen_condition", "balcony"],
    ["backyard", "backyard_privacy", "view", "noise_level"],
    ["sunlight_rating", "living_room_direction", "bedroom_direction", "backyard_direction"],
]
SELECTS = {
    "property_type": ["", *PropertyType.values()],
    "listing_status": ["", *ListingStatus.values()],
}
# Free text stays free; these are only hints shown inside the empty box.
PLACEHOLDERS = {
    "sunlight_rating": "e.g. Poor / Average / Good / Great, or your own note",
}


def friendly_errors(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        loc = e["loc"][0] if e["loc"] else "value"
        out.append(f"{LABELS.get(str(loc), loc)}: {e['msg'].removeprefix('Value error, ')}")
    return out