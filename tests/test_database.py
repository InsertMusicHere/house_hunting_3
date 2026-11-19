from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.enums import IngestionMethod
from database.models import DataSource, ExtractionLog, FinancialDetails, ListingHistory, Property, PropertyFeatures
from database.repository import create_property, find_duplicates, update_property_from_new_listing
from database.seed import seed_data_sources
from schemas.property import PropertyExtract


def make(**kw):
    base = dict(
        listing_url="https://www.zolo.ca/toronto-real-estate/12-maple-st/1",
        listing_number="C1234567", listing_status="For Sale", address="12 Maple Street",
        city="Toronto", postal_code="M5V 2T6", property_type="Detached",
        asking_price="$950,000", bedrooms=3, bathrooms=2,
        interior_size_min=1500, interior_size_max=2000, description="Bright home.",
    )
    base.update(kw)
    return PropertyExtract(**base)


def test_seed_is_idempotent_and_marks_terms(session):
    assert seed_data_sources(session) == 5
    assert seed_data_sources(session) == 0
    names = {s.name: s for s in session.scalars(select(DataSource))}
    assert names["HouseSigma"].terms_status == "automated_retrieval_not_permitted"
    assert names["Realtor.ca"].terms_url is None  # not read directly; no invented URL


def test_create_property_writes_history_and_source(session):
    seed_data_sources(session)
    prop = create_property(session, make(), ingestion_method=IngestionMethod.MANUAL_PASTE)
    session.commit()
    assert prop.source.name == "Zolo"
    assert prop.asking_price == Decimal("950000.00")
    assert prop.description == "Bright home."
    assert len(prop.history) == 1 and prop.history[0].change_note == "Initial entry"
    assert "year_built" in prop.missing_fields  # missing stays NULL and is listed
    assert prop.year_built is None


def test_duplicates_found_by_each_rule(session):
    create_property(session, make(), ingestion_method=IngestionMethod.MANUAL_PASTE)
    session.commit()
    by_mls = find_duplicates(session, listing_number="C1234567", address=None, unit=None,
                             postal_code=None, listing_url=None)
    assert [m.reasons for m in by_mls] == [("listing_number",)]
    by_addr = find_duplicates(session, listing_number=None, address="12 maple st.", unit=None,
                              postal_code="m5v2t6", listing_url=None)
    assert [m.reasons for m in by_addr] == [("address",)]
    by_url = find_duplicates(session, listing_number=None, address=None, unit=None, postal_code=None,
                             listing_url="zolo.ca/toronto-real-estate/12-maple-st/1/")
    assert [m.reasons for m in by_url] == [("url",)]
    assert find_duplicates(session, listing_number="X", address="99 Other Rd", unit=None,
                           postal_code="M5V 2T6", listing_url=None) == []


def test_db_blocks_duplicate_mls_and_address(session):
    create_property(session, make(), ingestion_method=IngestionMethod.MANUAL_PASTE)
    session.commit()
    with pytest.raises(IntegrityError):
        create_property(session, make(address="500 Other Ave", postal_code="M4M 1A1"),
                        ingestion_method=IngestionMethod.MANUAL_PASTE)  # same MLS number
    session.rollback()
    with pytest.raises(IntegrityError):
        create_property(session, make(listing_number="C999"), ingestion_method=IngestionMethod.MANUAL_PASTE)  # same address
    session.rollback()


def test_missing_mls_and_address_key_do_not_collide(session):
    a = make(listing_number=None, address=None, postal_code=None, listing_url=None)
    b = make(listing_number=None, address=None, postal_code=None, listing_url=None)
    create_property(session, a, ingestion_method=IngestionMethod.MANUAL_ENTRY)
    create_property(session, b, ingestion_method=IngestionMethod.MANUAL_ENTRY)
    session.commit()  # NULL keys are exempt from the unique indexes
    assert session.query(Property).count() == 2


def test_update_appends_history_and_respects_manual_overrides(session):
    prop = create_property(session, make(), ingestion_method=IngestionMethod.MANUAL_PASTE)
    prop.manual_overrides = {"bedrooms": True}
    prop.bedrooms = 4  # user corrected this
    session.commit()
    update_property_from_new_listing(session, prop, make(asking_price="$929,000", bedrooms=3))
    session.commit()
    assert prop.asking_price == Decimal("929000.00")
    assert prop.bedrooms == 4  # protected
    prices = [h.asking_price for h in prop.history]
    assert prices == [Decimal("950000.00"), Decimal("929000.00")]  # history kept, not overwritten


def test_check_constraints_reject_bad_values(session):
    with pytest.raises(IntegrityError):
        session.add(Property(ingestion_method="scraped_by_bot"))
        session.flush()
    session.rollback()
    with pytest.raises(IntegrityError):
        session.add(Property(ingestion_method="manual_entry", property_type="Condo Apt"))
        session.flush()
    session.rollback()


def test_related_tables_and_cascade(session):
    prop = create_property(session, make(), ingestion_method=IngestionMethod.MANUAL_PASTE)
    prop.features = PropertyFeatures(kitchen_condition="Renovated",
                                     ai_detail={"kitchen_condition": {"value": "Renovated",
                                                                      "source_type": "description",
                                                                      "confidence": 0.85}})
    prop.financials.append(FinancialDetails(expected_purchase_price=Decimal("930000"),
                                            mortgage_rate=Decimal("4.79")))
    session.add(ExtractionLog(property_id=prop.id, ingestion_method="manual_paste", status="success"))
    session.commit()
    assert prop.features.ai_detail["kitchen_condition"]["confidence"] == 0.85
    session.delete(prop)
    session.commit()
    assert session.query(PropertyFeatures).count() == 0
    assert session.query(FinancialDetails).count() == 0
    assert session.query(ListingHistory).count() == 0
    log = session.scalars(select(ExtractionLog)).one()
    assert log.property_id is None  # log survives, link cleared


def test_one_features_row_and_unique_scenario(session):
    prop = create_property(session, make(), ingestion_method=IngestionMethod.MANUAL_PASTE)
    session.add_all([FinancialDetails(property_id=prop.id, scenario_name="A"),
                     FinancialDetails(property_id=prop.id, scenario_name="A")])
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
