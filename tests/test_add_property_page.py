"""Drives the real Add Property page with Streamlit's AppTest, on a temporary SQLite file."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

import database.connection as conn
from database.models import Base, ExtractionLog, ListingHistory, Property
from database.seed import seed_data_sources

ROOT = Path(__file__).resolve().parent.parent
HS = (ROOT / "tests/fixtures/housesigma_synthetic.txt").read_text(encoding="utf-8")
RT = (ROOT / "tests/fixtures/realtor_synthetic.txt").read_text(encoding="utf-8")


@pytest.fixture()
def app(tmp_path):
    url = f"sqlite:///{tmp_path/'t.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_data_sources(s)
        s.commit()
    conn._engine = None
    at = AppTest.from_file(str(ROOT / "pages/1_Add_Property.py"), default_timeout=60)
    at.secrets["auth"] = {"username": "admin", "password_hash": "x"}
    at.secrets["database"] = {"url": url}
    at.session_state["authenticated"] = True
    at.engine = engine
    yield at
    conn._engine = None
    engine.dispose()


def click(at, label):
    [b for b in at.button if b.label == label][0].click().run()
    assert not at.exception, at.exception


def rows(at, model):
    with Session(at.engine) as s:
        return list(s.scalars(select(model)))


def test_login_required():
    at = AppTest.from_file(str(ROOT / "pages/1_Add_Property.py"), default_timeout=30)
    at.secrets["auth"] = {"username": "admin", "password_hash": "x"}
    at.run()
    assert any("Sign in" in s.value for s in at.subheader)


def test_full_flow_paste_review_save_duplicate_update(app):
    at = app.run()
    assert at.session_state.stage == "input"

    # --- paste two sources and extract
    at.text_area(key="paste_1_0").set_value(RT)
    at.text_area(key="paste_2_0").set_value(HS)
    at.text_input(key="in_url_0").set_value("https://www.realtor.ca/real-estate/1/x/")
    click(at, "Extract and review")
    assert at.session_state.stage == "review"
    assert at.text_input(key="rv_address_0").value == "123 Example Road W"
    assert at.text_input(key="rv_postal_code_0").value == "L6X 1A1"
    assert at.text_input(key="rv_maintenance_fee_0").value == "499.75"
    assert any("Sources disagree" in c.value for c in at.caption)

    # --- the user adds a manual-only value, then saves
    at.text_input(key="rv_sunlight_rating_0").set_value("Great - morning sun")
    at.text_input(key="rv_direction_0").set_value("North")
    click(at, "Save")
    assert at.session_state.stage == "saved"
    props = rows(at, Property)
    assert len(props) == 1
    p = props[0]
    assert p.direction == "North" and p.extraction_status == "verified" and p.postal_code == "L6X 1A1"
    assert len(rows(at, ListingHistory)) == 5
    assert rows(at, ExtractionLog)[0].property_id == p.id
    with Session(at.engine) as s:
        assert s.get(Property, p.id).features.sunlight_rating == "Great - morning sun"

    # --- add the same listing again: duplicate flow offers Update / Cancel
    click(at, "Add another property")
    assert at.session_state.stage == "input" and at.session_state.nonce == 1
    at.text_area(key="paste_1_1").set_value(RT)
    click(at, "Extract and review")
    at.text_input(key="rv_asking_price_1").set_value("480000")
    click(at, "Save")
    assert at.session_state.stage == "duplicate"
    assert any("may already exist" in t.value for t in at.title)
    click(at, "Update existing record")
    assert at.session_state.stage == "saved"
    props = rows(at, Property)
    assert len(props) == 1 and str(props[0].asking_price) == "480000.00"
    assert any("Updated" in s.value for s in at.success)


def test_duplicate_cancel_returns_to_review_with_values(app):
    at = app.run()
    at.text_area(key="paste_1_0").set_value(RT)
    click(at, "Extract and review")
    click(at, "Save and add another")
    assert at.session_state.stage == "input"
    assert any("Saved" in s.value for s in at.success)
    at.text_area(key="paste_1_1").set_value(RT)
    click(at, "Extract and review")
    at.text_input(key="rv_bedrooms_1").set_value("5")
    click(at, "Save")
    assert at.session_state.stage == "duplicate"
    click(at, "Cancel")
    assert at.session_state.stage == "review"
    assert at.text_input(key="rv_bedrooms_1").value == "5"  # edits survive Cancel
    assert len(rows(at, Property)) == 1


def test_apartment_rejected_and_logged(app):
    at = app.run()
    at.text_area(key="paste_1_0").set_value(RT.replace("Row / Townhouse", "Apartment"))
    click(at, "Extract and review")
    assert at.session_state.stage == "input"
    assert any("rejected" in e.value for e in at.error)
    assert [l.status for l in rows(at, ExtractionLog)] == ["rejected"]
    assert rows(at, Property) == []


def test_validation_errors_block_save(app):
    at = app.run()
    at.text_area(key="paste_1_0").set_value(RT)
    click(at, "Extract and review")
    at.text_input(key="rv_postal_code_0").set_value("12345")
    at.text_input(key="rv_city_0").set_value("")
    click(at, "Save")
    assert at.session_state.stage == "review"
    assert any("Required" in e.value for e in at.error)
    at.text_input(key="rv_city_0").set_value("Brampton")
    click(at, "Save")
    assert any("Postal code" in m.value for m in at.markdown) or any("postal" in e.value.lower() for e in at.error)
    assert rows(at, Property) == []


def test_manual_entry_and_unrecognized_text(app):
    at = app.run()
    at.text_area(key="paste_1_0").set_value("totally unknown layout")
    click(at, "Extract and review")
    assert at.session_state.stage == "review"
    assert any("not recognized" in w.value for w in at.warning)
    at.text_input(key="rv_address_0").set_value("5 Test Street")
    at.text_input(key="rv_city_0").set_value("Toronto")
    click(at, "Save")
    p = rows(at, Property)[0]
    assert p.ingestion_method == "manual_entry" and p.address == "5 Test Street"
    assert p.manual_overrides.get("address") is True


def test_nothing_pasted_warns(app):
    at = app.run()
    click(at, "Extract and review")
    assert any("Paste some listing text" in w.value for w in at.warning)
