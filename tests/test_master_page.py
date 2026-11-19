"""Drives the real Property Master page with Streamlit's AppTest, on a temporary SQLite file.

AppTest cannot click a table row or press a browser download, so rows are opened through the
"Open a property" list and the export files are checked in test_export.py.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

import database.connection as conn
from database.models import Base, Property
from database.seed import seed_data_sources
from tests.master_helpers import add

ROOT = Path(__file__).resolve().parent.parent


def build_app(tmp_path, populate):
    url = f"sqlite:///{tmp_path/'t.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_data_sources(s)
        s.commit()
        populate(s)
    conn._engine = None
    at = AppTest.from_file(str(ROOT / "pages/2_Property_Master.py"), default_timeout=60)
    at.secrets["auth"] = {"username": "admin", "password_hash": "x"}
    at.secrets["database"] = {"url": url}
    at.session_state["authenticated"] = True
    at.engine = engine
    return at


def populate_three(s):
    add(s, address="12 Maple Street", city="Toronto", listing_number="C1", asking_price="$900,000",
        features={"flooring": "Oak"})
    add(s, address="99 Oak Avenue", city="Mississauga", listing_number="C2", postal_code="L5G 1A1",
        asking_price="$600,000")
    add(s, address="5 Pine Court", city="Toronto", listing_number="C3", postal_code="M4E 1A1", asking_price=None)


@pytest.fixture()
def app(tmp_path):
    at = build_app(tmp_path, populate_three)
    yield at
    conn._engine = None
    at.engine.dispose()


def props(at):
    with Session(at.engine) as s:
        return {p.listing_number: p for p in s.scalars(select(Property))}, s


def pid(at, mls):
    with Session(at.engine) as s:
        return str(s.scalar(select(Property.id).where(Property.listing_number == mls)))


def click(at, label):
    [b for b in at.button if b.label == label][0].click().run()
    assert not at.exception, at.exception


def open_property(at, mls):
    at.selectbox(key="pm_pick_0").select(pid(at, mls)).run()
    assert not at.exception, at.exception


def test_login_required():
    at = AppTest.from_file(str(ROOT / "pages/2_Property_Master.py"), default_timeout=30)
    at.secrets["auth"] = {"username": "admin", "password_hash": "x"}
    at.run()
    assert any("Sign in" in s.value for s in at.subheader)


def test_empty_database_shows_a_hint(tmp_path):
    at = build_app(tmp_path, lambda s: None).run()
    assert not at.exception and any("No properties saved yet" in i.value for i in at.info)
    conn._engine = None


def test_table_filters_sorts_and_column_picker_run_clean(app):
    at = app.run()
    assert not at.exception
    assert any("Showing 3 of 3" in c.value for c in at.caption)
    at.text_input(key="pm_text").set_value("maple").run()
    assert any("Showing 1 of 3" in c.value for c in at.caption)
    at.text_input(key="pm_text").set_value("").run()
    at.multiselect(key="pm_city").select("Toronto").run()
    assert any("Showing 2 of 3" in c.value for c in at.caption)
    at.number_input(key="pm_pmin").set_value(700000).run()
    assert any("Showing 1 of 3" in c.value for c in at.caption)
    assert any("blank value are left out" in c.value for c in at.caption)
    at.selectbox(key="pm_sort").select("asking_price").run()
    at.radio(key="pm_order").set_value("Ascending").run()
    at.multiselect(key="pm_cols").select("flooring").select("missing_fields").run()
    assert not at.exception


def test_open_property_shows_details_edit_and_delete_tabs(app):
    at = app.run()
    open_property(at, "C1")
    assert [t.label for t in at.tabs] == ["Details", "Edit", "Delete"]
    assert any(s.value.startswith("12 Maple Street") for s in at.subheader)
    assert at.text_input(key=f"ed_{pid(at, 'C1')}_0_address").value == "12 Maple Street"


def test_edit_saves_and_records_manual_change(app):
    at = app.run()
    open_property(at, "C1")
    base = f"ed_{pid(at, 'C1')}_0_"
    at.text_input(key=base + "neighbourhood").set_value("Yorkville")
    at.text_input(key=base + "asking_price").set_value("925000")
    at.text_input(key=base + "flooring").set_value("")        # cleared on purpose
    at.text_input(key=base + "sunlight_rating").set_value("Great")
    click(at, "Save changes")
    assert any("Saved changes to 12 Maple Street" in s.value for s in at.success)
    p = props(at)[0]["C1"]
    with Session(at.engine) as s:
        p = s.scalar(select(Property).where(Property.listing_number == "C1"))
        assert p.neighbourhood == "Yorkville" and str(p.asking_price) == "925000.00"
        assert p.manual_overrides == {"neighbourhood": True, "asking_price": True}
        assert p.features.flooring is None and p.features.sunlight_rating == "Great"
        assert p.features.ai_detail["flooring"]["source_type"] == "manual"
        assert p.extraction_status == "verified" and p.last_verified is not None


def test_edit_errors_are_shown_and_nothing_is_saved(app):
    at = app.run()
    open_property(at, "C1")
    base = f"ed_{pid(at, 'C1')}_0_"
    at.text_input(key=base + "asking_price").set_value("abc")
    at.text_input(key=base + "address").set_value("")
    at.text_input(key=base + "listing_number").set_value("C2")      # also collides with another property
    click(at, "Save changes")
    assert at.error and "Nothing was saved" in at.error[0].value
    assert "Asking price" in at.error[0].value and "Street address is required" in at.error[0].value
    with Session(at.engine) as s:
        p = s.scalar(select(Property).where(Property.address == "12 Maple Street"))
        assert str(p.asking_price) == "900000.00" and p.listing_number == "C1"


def test_edit_collision_message_names_the_other_property(app):
    at = app.run()
    open_property(at, "C1")
    at.text_input(key=f"ed_{pid(at, 'C1')}_0_listing_number").set_value("C2")
    click(at, "Save changes")
    assert "99 Oak Avenue" in at.error[0].value and "MLS number" in at.error[0].value


def test_delete_needs_confirmation_then_removes_only_that_property(app):
    at = app.run()
    open_property(at, "C3")
    delete_btn = [b for b in at.button if b.label == "Delete permanently"][0]
    assert delete_btn.disabled
    at.checkbox(key=f"del_ok_{pid(at, 'C3')}").check().run()
    assert not [b for b in at.button if b.label == "Delete permanently"][0].disabled
    click(at, "Delete permanently")
    assert any("Deleted 5 Pine Court" in s.value for s in at.success)
    with Session(at.engine) as s:
        assert sorted(p.listing_number for p in s.scalars(select(Property))) == ["C1", "C2"]
    assert any("Showing 2 of 2" in c.value for c in at.caption)


def test_maintenance_button_backfills_quality(app):
    with Session(app.engine) as s:
        for p in s.scalars(select(Property)):
            p.data_quality_score = None
        s.commit()
    at = app.run()
    click(at, "Recalculate stored data-quality scores")
    assert any("Recalculated data quality for 3" in s.value for s in at.success)
    with Session(app.engine) as s:
        assert all(p.data_quality_score is not None for p in s.scalars(select(Property)))