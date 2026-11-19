"""Property Master service: rows, derived columns, filters, sorting, quality score."""
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from database import repository as repo
from schemas.features import TEXT_FIELDS
from schemas.property import TRACKED_FIELDS
from services import ingestion as svc
from services import property_master as pm
from tests.master_helpers import add
from utils.form_config import FACT_LAYOUT, FEATURE_LAYOUT, MY_LAYOUT
from utils.quality import percent, quality_for_property


@pytest.fixture()
def df(session):
    add(session, address="12 Maple Street", city="Toronto", neighbourhood="Annex", listing_number="C1",
        asking_price="$900,000", bedrooms=3, bedrooms_extra=1, bathrooms=2, maintenance_fee="450.50",
        interior_size_min=1000, interior_size_max=1000, features={"flooring": "Hardwood"})
    add(session, address="99 Oak Avenue", city="Mississauga", neighbourhood="Port Credit", listing_number="C2",
        postal_code="L5G 1A1", property_type="Condo Townhouse", asking_price="$600,000", bedrooms=2,
        bathrooms=1, maintenance_fee="300", interior_size_min=800, interior_size_max=1000)
    add(session, address="5 Pine Court", city="Toronto", neighbourhood="Beaches", listing_number="C3",
        postal_code="M4E 1A1", asking_price=None, bedrooms=None, bathrooms=None, maintenance_fee=None,
        interior_size_min=None, interior_size_max=None, description=None)
    rows = [pm.property_row(p) for p in repo.list_properties(session)]
    return pm.build_frame(rows)


def ids(frame):
    return sorted(frame["listing_number"])


# ---------------------------------------------------------------------------- layout coverage
def test_forms_cover_every_edited_field():
    flat = {n for layout in (FACT_LAYOUT, MY_LAYOUT) for row in layout for n in row} | {"description"}
    assert flat == set(svc.PROPERTY_FIELDS)
    assert {n for row in FEATURE_LAYOUT for n in row} | {"property_features"} == set(svc.FEATURE_FIELDS)


def test_every_feature_field_has_a_table_column():
    assert set(TEXT_FIELDS) <= {c.key for c in pm.COLUMNS}
    assert set(pm.DEFAULT_COLUMNS) <= {c.key for c in pm.TABLE_COLUMNS}
    assert "description" not in pm.DEFAULT_COLUMNS and "description" not in {c.key for c in pm.TABLE_COLUMNS}


# ---------------------------------------------------------------------------- quality score
def test_quality_counts_filled_tracked_fields(session):
    full = add(session, listing_number="F1")
    score, missing = quality_for_property(full)
    assert len(TRACKED_FIELDS) == 15
    assert score == Decimal("0.87") and sorted(missing) == ["parking", "year_built"] and percent(score) == 87
    assert full.data_quality_score == score  # stored at save time
    assert set(full.missing_fields) == set(missing)


def test_quality_treats_blank_text_as_missing(session):
    p = add(session, listing_number="F2", description=None)
    assert "description" in p.missing_fields
    assert percent(quality_for_property(p)[0]) == 80


def test_recalculate_all_backfills_old_records(session):
    p = add(session, listing_number="F3")
    p.data_quality_score = None
    p.missing_fields = None
    session.commit()
    assert repo.recalculate_all_quality(session) == 1
    assert p.data_quality_score == Decimal("0.87") and p.missing_fields


# ---------------------------------------------------------------------------- rows and derived columns
def test_price_per_sqft_is_derived_and_blank_without_size():
    assert pm.price_per_sqft(Decimal("900000"), 1000, 1000) == 900.0
    assert pm.price_per_sqft(Decimal("900000"), 800, 1000) == 1000.0   # midpoint of a range
    assert pm.price_per_sqft(Decimal("900000"), None, 1000) == 900.0   # only one bound given
    assert pm.price_per_sqft(Decimal("900000"), None, None) is None
    assert pm.price_per_sqft(None, 1000, 1000) is None


def test_rows_show_listed_bedroom_text_and_blanks(df):
    by = df.set_index("listing_number")
    assert by.loc["C1", "bedrooms"] == "3+1" and by.loc["C2", "bedrooms"] == "2"
    assert pd.isna(by.loc["C3", "bedrooms"]) and pd.isna(by.loc["C3", "price_per_sqft"])
    assert by.loc["C1", "price_per_sqft"] == 900.0
    assert by.loc["C1", "flooring"] == "Hardwood" and pd.isna(by.loc["C2", "flooring"])
    assert by.loc["C1", "data_source"] == "Manual entry"


def test_times_are_shown_in_toronto_time():
    summer = datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc)
    assert pm.to_local(summer).hour == 12 and pm.to_local(winter).hour == 11
    assert pm.to_local(datetime(2026, 7, 1, 16, 0)).hour == 12  # naive is treated as UTC
    assert pm.to_local(None) is None


def test_format_value_blank_is_dash():
    assert pm.format_value("money", None) == "—" and pm.format_value("money", float("nan")) == "—"
    assert pm.format_value("money", 450.5) == "$450.50" and pm.format_value("money", 900000.0) == "$900,000"
    assert pm.format_value("pct", 73) == "73%"


# ---------------------------------------------------------------------------- filters
def test_text_search_matches_every_word_across_fields(df):
    assert ids(pm.apply_filters(df, pm.Filters(text="maple"))) == ["C1"]
    assert ids(pm.apply_filters(df, pm.Filters(text="oak port"))) == ["C2"]       # address + neighbourhood
    assert ids(pm.apply_filters(df, pm.Filters(text="c3"))) == ["C3"]             # MLS number
    assert ids(pm.apply_filters(df, pm.Filters(text="m4e"))) == ["C3"]            # postal code
    assert ids(pm.apply_filters(df, pm.Filters(text="maple oak"))) == []


def test_category_filters(df):
    assert ids(pm.apply_filters(df, pm.Filters(cities=["Toronto"]))) == ["C1", "C3"]
    assert ids(pm.apply_filters(df, pm.Filters(neighbourhoods=["Port Credit"]))) == ["C2"]
    assert ids(pm.apply_filters(df, pm.Filters(property_types=["Condo Townhouse"]))) == ["C2"]
    assert ids(pm.apply_filters(df, pm.Filters(statuses=["For Sale"]))) == ["C1", "C2", "C3"]
    assert ids(pm.apply_filters(df, pm.Filters(extraction_statuses=["failed"]))) == []


def test_numeric_filters_exclude_blank_values(df):
    assert ids(pm.apply_filters(df, pm.Filters(price_min=700000))) == ["C1"]
    assert ids(pm.apply_filters(df, pm.Filters(price_max=700000))) == ["C2"]       # C3 has no price: not guessed
    assert ids(pm.apply_filters(df, pm.Filters(price_min=500000, price_max=950000))) == ["C1", "C2"]
    assert ids(pm.apply_filters(df, pm.Filters(fee_min=400))) == ["C1"]
    assert ids(pm.apply_filters(df, pm.Filters(fee_max=400))) == ["C2"]
    assert ids(pm.apply_filters(df, pm.Filters(bathrooms_min=2))) == ["C1"]


def test_min_bedrooms_counts_the_plus_one(df):
    assert ids(pm.apply_filters(df, pm.Filters(bedrooms_min=4))) == ["C1"]          # 3+1
    assert ids(pm.apply_filters(df, pm.Filters(bedrooms_min=2))) == ["C1", "C2"]
    assert pm.Filters(bedrooms_min=2).numeric_active() and not pm.Filters(text="x").numeric_active()


def test_only_missing_fields_filter(df):
    assert ids(pm.apply_filters(df, pm.Filters(only_missing=True))) == ["C1", "C2", "C3"]  # none is 100% complete
    assert pm.apply_filters(df, pm.Filters()).shape[0] == 3


def test_filters_combine(df):
    out = pm.apply_filters(df, pm.Filters(cities=["Toronto"], price_min=100000, text="maple"))
    assert ids(out) == ["C1"]


# ---------------------------------------------------------------------------- sorting
def test_sort_numeric_text_and_blanks_last(df):
    asc = pm.sort_frame(df, "asking_price", True)
    assert list(asc["listing_number"]) == ["C2", "C1", "C3"]                         # blank price last
    desc = pm.sort_frame(df, "asking_price", False)
    assert list(desc["listing_number"]) == ["C1", "C2", "C3"]                        # blank still last
    assert list(pm.sort_frame(df, "address", True)["address"]) == ["12 Maple Street", "5 Pine Court", "99 Oak Avenue"]


def test_sort_text_ignores_case(df):
    df.loc[df.listing_number == "C2", "city"] = "alpha"
    assert list(pm.sort_frame(df, "city", True)["city"])[0] == "alpha"


def test_sort_rejects_unknown_column(df):
    with pytest.raises(ValueError):
        pm.sort_frame(df, "_id", True)


def test_manual_override_column_lists_property_and_feature_fields(session):
    p = add(session, listing_number="M1", features={"flooring": "Oak"},
            features_detail={"flooring": {"value": "Oak", "source_type": "manual"}})
    p.manual_overrides = {"direction": True}
    session.commit()
    row = pm.property_row(p)
    assert row["manual_overrides"] == "Direction, Flooring"