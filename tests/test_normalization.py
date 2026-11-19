from decimal import Decimal

import pytest

from database.enums import PropertyType
from utils.normalization import (
    OutOfScopeProperty,
    build_address_key,
    canonicalize_url,
    classify_property_type,
    domain_of,
    normalize_postal_code,
    parse_bedrooms,
    parse_money,
    parse_size_range,
)


@pytest.mark.parametrize("raw,expected", [
    ("Detached", PropertyType.DETACHED),
    ("Semi-Detached", PropertyType.SEMI_DETACHED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("Link", PropertyType.LINK),
    ("Att/Row/Townhouse", PropertyType.FREEHOLD_TOWNHOUSE),
    ("Freehold Townhouse", PropertyType.FREEHOLD_TOWNHOUSE),
    ("Condo Townhouse", PropertyType.CONDO_TOWNHOUSE),
    ("Townhouse Condo", PropertyType.CONDO_TOWNHOUSE),
    ("Vacant Land", None),
    ("Single Family", None),
    ("", None),
    (None, None),
])
def test_property_type(raw, expected):
    assert classify_property_type(raw) == expected


@pytest.mark.parametrize("raw", ["Condo Apt", "Apartment", "Co-Op Apt"])
def test_apartments_rejected(raw):
    with pytest.raises(OutOfScopeProperty):
        classify_property_type(raw)


def test_money():
    assert parse_money("$1,299,000") == Decimal("1299000")
    assert parse_money("899000.50") == Decimal("899000.50")
    assert parse_money("call for price") is None
    assert parse_money(None) is None


def test_size_range():
    assert parse_size_range("1500-2000 sqft") == (1500, 2000)
    assert parse_size_range("1,800") == (1800, 1800)
    assert parse_size_range("150 m2") is None  # never converted silently
    assert parse_size_range("n/a") is None
    assert parse_size_range("700 800 900") is None


def test_bedrooms():
    assert parse_bedrooms("3+1") == (3, 1)
    assert parse_bedrooms("4") == (4, None)
    assert parse_bedrooms("three") is None


def test_postal_code():
    assert normalize_postal_code("m5v2t6") == "M5V 2T6"
    assert normalize_postal_code("M5V 2T6") == "M5V 2T6"
    assert normalize_postal_code("V6B 1A1") is None  # not Ontario
    assert normalize_postal_code("M5D 2T6") is None  # D never used
    assert normalize_postal_code(None) is None


def test_address_key_ignores_formatting():
    a = build_address_key("12 Maple Street", "3", "m5v 2t6")
    b = build_address_key("12 maple st.", "3", "M5V2T6")
    assert a == b and a is not None
    assert build_address_key("12 Maple St", "4", "M5V 2T6") != a  # different unit


def test_address_key_requires_address_and_postal():
    assert build_address_key("12 Maple St", None, None) is None
    assert build_address_key(None, None, "M5V 2T6") is None


def test_url_canonical():
    assert canonicalize_url("https://www.Realtor.ca/real-estate/123/x/?a=1#top") == "realtor.ca/real-estate/123/x"
    assert canonicalize_url("realtor.ca/real-estate/123/x/") == "realtor.ca/real-estate/123/x"
    assert canonicalize_url("") is None
    assert domain_of("https://www.zolo.ca/toronto-real-estate/1") == "zolo.ca"


def test_money_keeps_negative_sign():
    assert parse_money("-5") == Decimal("-5")
    assert parse_money("($1,200)") == Decimal("-1200")
