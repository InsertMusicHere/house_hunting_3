"""Editing and deleting saved properties: validation, collisions, manual-override markers, cascade."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from database import repository as repo
from database.seed import seed_data_sources
from database.models import ExtractionLog, FinancialDetails, ListingHistory, Property, PropertyFeatures
from schemas.property import PropertyExtract
from services import ingestion as svc
from services import property_edit as edit
from tests.master_helpers import add, make


def run_edit(session, prop, vals=None, feats=None, url=None):
    """Open the form for prop, change some fields, validate and save. Returns (check, errors)."""
    init = edit.form_values(prop)
    fin_vals, fin_feats = dict(init[0]), dict(init[1])
    fin_vals.update(vals or {})
    fin_feats.update(feats or {})
    ref = prop.listing_url or "" if url is None else url
    check = edit.check_edit(session, prop, fin_vals, fin_feats, ref)
    errors = edit.save_edit(session, prop, check, init, (fin_vals, fin_feats)) if check.ok else check.errors
    return check, errors


# ---------------------------------------------------------------------------- happy path
def test_unchanged_save_marks_verified_and_records_no_overrides(session):
    p = add(session, features={"flooring": "Hardwood"})
    p.extraction_status = "extracted"
    p.last_verified = None
    session.commit()
    _, errors = run_edit(session, p)
    session.commit()
    assert errors == []
    assert p.extraction_status == "verified" and p.last_verified is not None
    assert not p.manual_overrides and "flooring" not in (p.features.ai_detail or {})
    assert p.asking_price == Decimal("950000.00")  # form text round-trips without drift


def test_changed_fields_are_recorded_as_manual_and_others_untouched(session):
    p = add(session)
    old_key = p.address_key
    _, errors = run_edit(session, p, {"asking_price": "925000", "neighbourhood": "Yorkville", "direction": "North"})
    session.commit()
    assert errors == []
    assert p.asking_price == Decimal("925000.00") and p.neighbourhood == "Yorkville" and p.direction == "North"
    assert p.manual_overrides == {"asking_price": True, "neighbourhood": True, "direction": True}
    assert p.bedrooms == 3 and p.address == "12 Maple Street" and p.address_key == old_key


def test_edit_does_not_add_history_rows(session):
    p = add(session)
    before = len(p.history)
    run_edit(session, p, {"asking_price": "925000"})
    session.commit()
    assert len(p.history) == before


def test_quality_and_missing_fields_follow_the_edit(session):
    p = add(session)
    assert p.data_quality_score == Decimal("0.87")
    run_edit(session, p, {"year_built": "1998", "parking": "Garage"})
    session.commit()
    assert p.data_quality_score == Decimal("1.00") and p.missing_fields == []
    run_edit(session, p, {"neighbourhood": ""})
    session.commit()
    assert p.missing_fields == ["neighbourhood"] and p.data_quality_score == Decimal("0.93")


def test_identity_edit_recomputes_address_key(session):
    p = add(session)
    run_edit(session, p, {"address": "14 Maple Street", "unit": "3"})
    session.commit()
    assert p.address_key.startswith("14 maple") and "|3|" in p.address_key


def test_reference_link_can_be_changed_and_cleared(session):
    seed_data_sources(session)
    p = add(session)
    run_edit(session, p, url="https://www.realtor.ca/real-estate/9/x/")
    session.commit()
    assert p.listing_url == "https://www.realtor.ca/real-estate/9/x/" and p.canonical_url == "realtor.ca/real-estate/9/x"
    assert p.source.name == "Realtor.ca"
    run_edit(session, p, url="")
    session.commit()
    assert p.listing_url is None and p.canonical_url is None


# ---------------------------------------------------------------------------- clearing a field
def test_clearing_property_fields_writes_null_and_is_remembered(session):
    p = add(session)
    _, errors = run_edit(session, p, {"asking_price": "", "description": ""})
    session.commit()
    assert errors == [] and p.asking_price is None and p.description is None
    assert p.manual_overrides["asking_price"] is True
    assert "asking_price" in p.missing_fields


def test_clearing_a_feature_is_remembered_and_never_refilled(session):
    p = add(session, features={"flooring": "Hardwood", "basement": "Finished"},
            features_detail={"flooring": {"value": "Hardwood", "source_type": "listing_field", "source": "Realtor.ca"}})
    run_edit(session, p, feats={"flooring": ""})
    session.commit()
    assert p.features.flooring is None
    assert p.features.ai_detail["flooring"] == {"value": None, "source_type": "manual"}

    # A later listing update carries flooring again: the cleared field must stay empty.
    incoming = svc.FeaturesInput(flooring="Carpet", basement="Unfinished", balcony="Yes")
    repo.fill_missing_features(session, p, incoming, {"flooring": {"value": "Carpet", "source_type": "listing_field"}})
    session.commit()
    assert p.features.flooring is None
    assert p.features.basement == "Finished"          # existing values were never overwritten
    assert p.features.balcony == "Yes"                # untouched blanks are still filled
    assert p.features.ai_detail["flooring"]["source_type"] == "manual"


def test_feature_added_by_hand_creates_features_row(session):
    p = add(session)
    assert p.features is None
    run_edit(session, p, feats={"sunlight_rating": "Great", "property_features": "Pool, Garage"})
    session.commit()
    assert p.features.sunlight_rating == "Great" and p.features.property_features == ["Pool", "Garage"]
    assert p.features.ai_detail["sunlight_rating"]["source_type"] == "manual"
    assert session.query(PropertyFeatures).count() == 1


def test_no_empty_features_row_is_created_when_nothing_is_entered(session):
    p = add(session)
    run_edit(session, p, {"direction": "East"})
    session.commit()
    assert p.features is None


# ---------------------------------------------------------------------------- validation
def test_required_fields(session):
    p = add(session)
    check, errors = run_edit(session, p, {"address": " ", "city": ""})
    assert not check.ok and "Street address is required." in errors and "City is required." in errors


def test_invalid_values_report_friendly_messages_and_write_nothing(session):
    p = add(session)
    _, errors = run_edit(session, p, {"asking_price": "abc", "bedrooms": "99", "postal_code": "ZZZ"})
    assert any(e.startswith("Asking price (CAD):") for e in errors)
    assert any(e.startswith("Bedrooms:") for e in errors) and any(e.startswith("Postal code:") for e in errors)
    session.rollback()
    assert p.asking_price == Decimal("950000.00") and p.postal_code == "M5V 2T6"


def test_length_limits_are_explained_not_silently_cut(session):
    p = add(session)
    _, errors = run_edit(session, p, {"direction": "North-northeast corner"}, {"flooring": "x" * 200})
    assert "Direction (your entry) is 22 characters; the limit is 20." in errors
    assert "Flooring is 200 characters; the limit is 150." in errors
    session.rollback()
    assert p.direction is None


def test_length_check_ignores_unlimited_text(session):
    assert edit.length_errors({"description": "y" * 50000}, {"property_features": "a, b"}) == []


# ---------------------------------------------------------------------------- collisions
def test_edit_cannot_duplicate_another_mls_number(session):
    add(session, listing_number="C1", address="1 First St", postal_code="M5V 2T6")
    b = add(session, listing_number="C2", address="2 Second St", postal_code="M4E 1A1")
    check, errors = run_edit(session, b, {"listing_number": "C1"})
    assert not check.ok and "1 First St" in errors[0] and "MLS number" in errors[0]


def test_edit_cannot_duplicate_another_address_unit_postal(session):
    add(session, listing_number="C1", address="1 First St", unit="5", postal_code="M5V 2T6")
    b = add(session, listing_number="C2", address="2 Second St", unit="5", postal_code="M5V 2T6")
    _, errors = run_edit(session, b, {"address": "1 First St"})
    assert errors and "address, unit and postal code" in errors[0]
    _, errors = run_edit(session, b, {"address": "1 First St", "unit": "6"})   # different unit: allowed
    assert errors == []


def test_keeping_your_own_identity_is_not_a_collision(session):
    p = add(session)
    _, errors = run_edit(session, p, {"neighbourhood": "Yorkville"})
    assert errors == []


def test_database_collision_after_validation_is_reported_and_rolled_back(session):
    a = add(session, listing_number="C1", address="1 First St", postal_code="M5V 2T6")
    b = add(session, listing_number="C2", address="2 Second St", postal_code="M4E 1A1")
    init = edit.form_values(b)
    fin = ({**init[0], "listing_number": "C1"}, init[1])
    check = edit.check_edit(session, b, {**init[0], "listing_number": "C3"}, init[1], "")  # valid when checked
    assert check.ok
    check.data = make(listing_number="C1", address="2 Second St", postal_code="M4E 1A1")   # collides at save time
    errors = edit.save_edit(session, b, check, init, fin)
    assert errors and "Nothing was changed" in errors[0]
    session.rollback()
    assert session.get(Property, b.id).listing_number == "C2"


# ---------------------------------------------------------------------------- delete
def test_delete_removes_children_and_keeps_logs_unlinked(session):
    p = add(session, features={"flooring": "Oak"})
    p.financials.append(FinancialDetails(expected_purchase_price=Decimal("930000")))
    session.add(ExtractionLog(property_id=p.id, ingestion_method="manual_paste", status="success"))
    session.commit()
    summary = edit.delete_summary(p)
    assert summary == {"history_rows": 1, "financial_scenarios": 1, "logs_kept": 1}
    edit.delete(session, p)
    session.commit()
    assert session.query(Property).count() == 0
    assert session.query(PropertyFeatures).count() == 0 and session.query(FinancialDetails).count() == 0
    assert session.query(ListingHistory).count() == 0
    assert session.scalars(select(ExtractionLog)).one().property_id is None


def test_delete_leaves_other_properties_alone(session):
    keep = add(session, listing_number="K1", address="1 Keep St", postal_code="M5V 2T6", features={"flooring": "Oak"})
    gone = add(session, listing_number="G1", address="2 Gone St", postal_code="M4E 1A1")
    edit.delete(session, gone)
    session.commit()
    assert [p.listing_number for p in session.scalars(select(Property))] == ["K1"]
    assert keep.features.flooring == "Oak" and len(keep.history) == 1