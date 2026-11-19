from decimal import Decimal

import pytest
from pydantic import ValidationError

from database.enums import ListingStatus, PropertyType
from schemas.property import PropertyExtract


def test_minimal_listing_keeps_missing_as_none():
    p = PropertyExtract(address="12 Maple St")
    assert p.asking_price is None and p.bedrooms is None
    assert "asking_price" in p.missing_fields()
    assert "address" not in p.missing_fields()


def test_normalizes_values():
    p = PropertyExtract(
        asking_price="$899,000", postal_code="m5v2t6", property_type="Att/Row/Townhouse",
        listing_status="For Sale", listing_url="https://www.zolo.ca/x/1/",
    )
    assert p.asking_price == Decimal("899000")
    assert p.postal_code == "M5V 2T6"
    assert p.property_type == PropertyType.FREEHOLD_TOWNHOUSE
    assert p.listing_status == ListingStatus.FOR_SALE
    assert p.canonical_url == "zolo.ca/x/1"


def test_blank_strings_become_none():
    p = PropertyExtract(city="  ", asking_price="")
    assert p.city is None and p.asking_price is None


def test_apartment_rejected():
    with pytest.raises(ValidationError):
        PropertyExtract(property_type="Condo Apt")


def test_unrecognized_type_is_none_not_guessed():
    assert PropertyExtract(property_type="Vacant Land").property_type is None


def test_bad_postal_code_rejected():
    with pytest.raises(ValidationError):
        PropertyExtract(postal_code="12345")


def test_size_order_and_ranges():
    with pytest.raises(ValidationError):
        PropertyExtract(interior_size_min=2000, interior_size_max=1500)
    p = PropertyExtract(interior_size_min=1500, interior_size_max=2000)
    assert "interior_size_min" not in p.missing_fields()


def test_negative_price_rejected():
    with pytest.raises(ValidationError):
        PropertyExtract(asking_price="-5")


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        PropertyExtract(mystery_field=1)
