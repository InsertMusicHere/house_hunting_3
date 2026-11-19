"""Drives the real Financial Calculator page with Streamlit's AppTest on a temporary SQLite file,
plus the Property Master financial columns and the comparison export."""
import io
from decimal import Decimal as D
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

import database.connection as conn
from database import repository as repo
from database.models import Base, FinancialDetails, Property
from database.seed import seed_data_sources
from services import export_service as exp
from services import property_master as pm
from services import scenarios as sv
from services.financial_rules import load_rules
from tests.master_helpers import add

ROOT = Path(__file__).resolve().parent.parent
R = load_rules()


def build_app(tmp_path, populate):
    url = f"sqlite:///{tmp_path/'t.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_data_sources(s)
        s.commit()
        populate(s)
    conn._engine = None
    at = AppTest.from_file(str(ROOT / "pages/3_Financial_Calculator.py"), default_timeout=60)
    at.secrets["auth"] = {"username": "admin", "password_hash": "x"}
    at.secrets["database"] = {"url": url}
    at.session_state["authenticated"] = True
    at.engine = engine
    return at


def one_property(s):
    add(s, address="12 Maple Street", city="Toronto", listing_number="C1", asking_price="$500,000",
        maintenance_fee="0", listed_annual_property_tax="4,200")


@pytest.fixture()
def app(tmp_path):
    at = build_app(tmp_path, one_property)
    yield at
    conn._engine = None
    at.engine.dispose()


def db_scenarios(at):
    with Session(at.engine) as s:
        return list(s.scalars(select(FinancialDetails).order_by(FinancialDetails.scenario_name)))


def click(at, label):
    [b for b in at.button if b.label == label][0].click().run()
    assert not at.exception, at.exception


def key_prefix(at):
    return f"fc_{db_scenarios(at)[0].id}_0_"


def create_first(at):
    at.run()
    click(at, "Create the first scenario")


def enter_mortgage(at, rate=5.0, amort=25, pct=5.0, term=None, mtype="Fixed"):
    p = key_prefix(at)
    at.selectbox(key=p + "type").select(mtype)
    at.number_input(key=p + "rate").set_value(rate)
    at.number_input(key=p + "amort").set_value(amort)
    at.number_input(key=p + "dppct").set_value(pct)
    if term:
        at.number_input(key=p + "term").set_value(term)
    at.run()
    assert not at.exception, at.exception


def metrics(at):
    return {m.label: m.value for m in at.metric}


# ---------------------------------------------------------------------------- access and empty states
def test_login_required():
    at = AppTest.from_file(str(ROOT / "pages/3_Financial_Calculator.py"), default_timeout=30)
    at.secrets["auth"] = {"username": "admin", "password_hash": "x"}
    at.run()
    assert any("Sign in" in s.value for s in at.subheader)


def test_no_properties_shows_a_hint(tmp_path):
    at = build_app(tmp_path, lambda s: None).run()
    assert not at.exception and any("No properties saved yet" in i.value for i in at.info)
    conn._engine = None


def test_a_broken_rules_file_is_reported_not_crashed(app, monkeypatch, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("not = = toml")
    import services.financial_rules as fr
    monkeypatch.setattr(fr, "DEFAULT_PATH", bad)
    at = app.run()
    assert not at.exception and any("rules file could not be used" in e.value for e in at.error)


# ---------------------------------------------------------------------------- first scenario and live results
def test_first_scenario_is_created_prefilled_and_primary(app):
    create_first(app)
    sc = db_scenarios(app)[0]
    assert sc.scenario_name == "Default" and sc.is_primary and sc.expected_purchase_price == D("500000.00")
    p = key_prefix(app)
    assert app.number_input(key=p + "price").value == 500000.0
    assert app.number_input(key=p + "annual_property_tax").value == 4200.0
    assert app.number_input(key=p + "legal_fees").value == 2000.0
    assert app.number_input(key=p + "rate").value is None and app.selectbox(key=p + "type").value is None
    assert app.checkbox(key=p + "ftb").value is False


def test_results_appear_live_and_match_the_engine(app):
    create_first(app)
    assert any("Enter" in i.value and "to calculate" in i.value for i in app.info)
    enter_mortgage(app)
    m = metrics(app)
    assert m["Monthly mortgage payment"] == "$2,873.13"
    assert m["Total mortgage (with premium)"] == "$494,000.00"
    # 6,475 + 6,475 Toronto + 115.89 + 1,520 + 2,000 + 350 + 500 = 17,435.89; + 25,000 down
    assert m["Total cash required"] == "$42,435.89"
    assert m["Total monthly outgoing"] == "$3,223.13"            # 2,873.13 + 350 tax
    assert app.dataframe  # tables rendered
    assert any("unsaved" in x.value for x in app.markdown)       # nothing saved yet


def test_down_payment_dollars_and_percent_stay_in_step(app):
    create_first(app)
    p = key_prefix(app)
    app.number_input(key=p + "dppct").set_value(10.0).run()
    assert app.number_input(key=p + "dp").value == 50000.0
    app.number_input(key=p + "dp").set_value(30000.0).run()
    assert app.number_input(key=p + "dppct").value == 6.0
    app.number_input(key=p + "price").set_value(600000.0).run()    # dollars were edited last: they stay, percent follows
    assert app.number_input(key=p + "dp").value == 30000.0 and app.number_input(key=p + "dppct").value == 5.0
    app.number_input(key=p + "dppct").set_value(10.0).run()
    app.number_input(key=p + "price").set_value(700000.0).run()    # percent edited last: dollars follow
    assert app.number_input(key=p + "dp").value == 70000.0


def test_rule_breaks_are_blocked_with_a_clear_message(app):
    create_first(app)
    enter_mortgage(app, amort=30)
    assert any("first-time buyer" in e.value for e in app.error)
    assert "Total cash required" not in metrics(app)
    p = key_prefix(app)
    app.checkbox(key=p + "ftb").check().run()
    assert not app.error and metrics(app)["Total monthly outgoing"]
    app.number_input(key=p + "dppct").set_value(3.0).run()
    assert any("minimum down payment" in e.value for e in app.error)
    app.number_input(key=p + "dppct").set_value(25.0).run()
    assert any("does not need mortgage default insurance" in e.value for e in app.error)


def test_toronto_choice_changes_the_closing_costs(app):
    create_first(app)
    enter_mortgage(app)
    with_toronto = metrics(app)["Total cash required"]
    app.selectbox(key=key_prefix(app) + "tor").select("no").run()
    without = metrics(app)["Total cash required"]
    assert D(with_toronto.strip("$").replace(",", "")) - D(without.strip("$").replace(",", "")) == D("6590.89")  # 6,475 + 115.89


def test_term_and_variable_compounding(app):
    create_first(app)
    enter_mortgage(app, term=5, mtype="Variable")
    texts = " ".join(str(df.value) for df in app.dataframe)
    assert "Balance owing after 5-year term" in texts and "monthly compounding" in texts


# ---------------------------------------------------------------------------- saving
def test_save_stores_inputs_and_calculated_results(app):
    create_first(app)
    enter_mortgage(app, term=5)
    p = key_prefix(app)
    app.selectbox(key=p + "comp").select("monthly").run()
    app.number_input(key=p + "electricity").set_value(85.5).run()
    click(app, "Save scenario")
    assert any("Saved 'Default'" in s.value for s in app.success)
    sc = db_scenarios(app)[0]
    assert (sc.mortgage_rate, sc.mortgage_type, sc.amortization_years, sc.term_years) == (D("5.000"), "Fixed", 25, 5)
    assert sc.down_payment == D("25000.00") and sc.down_payment_pct == D("5.00") and sc.down_payment_basis == "percent"
    assert sc.compounding == "monthly" and sc.electricity == D("85.50") and sc.first_time_buyer is False
    assert sc.toronto_override is None
    assert sc.calculated["status"] == "ok" and sc.calculated["mortgage"]["compounding"] == "monthly"
    assert sc.calculated["monthly"]["electricity"] == "85.50" and sc.calculated_at is not None
    assert not any("unsaved" in x.value for x in app.markdown)


def test_blocked_and_incomplete_scenarios_can_still_be_saved(app):
    create_first(app)
    click(app, "Save scenario")
    assert db_scenarios(app)[0].calculated["status"] == "incomplete"


def test_discard_changes_reloads_the_saved_values(app):
    create_first(app)
    enter_mortgage(app)
    click(app, "Save scenario")
    p = key_prefix(app)
    app.number_input(key=p + "rate").set_value(7.0).run()
    click(app, "Discard changes")
    assert app.number_input(key=f"fc_{db_scenarios(app)[0].id}_1_rate").value == 5.0


def test_stale_warning_and_recalculate_button(app):
    create_first(app)
    enter_mortgage(app)
    click(app, "Save scenario")
    with Session(app.engine) as s:
        p = s.scalar(select(Property))
        p.asking_price = D("475000")
        s.commit()
    app.run()
    assert any("out of date" in w.value and "$500,000 to $475,000" in w.value for w in app.warning)
    click(app, "Recalculate this scenario")
    assert not any("out of date" in w.value for w in app.warning)


# ---------------------------------------------------------------------------- manage scenarios
def test_create_duplicate_rename_primary_and_delete(app):
    create_first(app)
    enter_mortgage(app)
    click(app, "Save scenario")
    prop_id = str(db_scenarios(app)[0].property_id)
    app.text_input(key=f"fc_new_{prop_id}").set_value("Alt").run()
    click(app, "Create")
    assert {s.scenario_name for s in db_scenarios(app)} == {"Default", "Alt"}
    alt = [s for s in db_scenarios(app) if s.scenario_name == "Alt"][0]
    assert not alt.is_primary and app.selectbox(key=f"fc_scenario_{prop_id}").value == str(alt.id)
    app.text_input(key=f"fc_ren_{alt.id}").set_value("Alt renamed").run()
    click(app, "Rename")
    click(app, "Set as primary")
    names = {s.scenario_name: s.is_primary for s in db_scenarios(app)}
    assert names == {"Default": False, "Alt renamed": True}
    app.text_input(key=f"fc_dup_{alt.id}").set_value("Copy").run()
    click(app, "Duplicate")
    assert {s.scenario_name for s in db_scenarios(app)} == {"Default", "Alt renamed", "Copy"}
    copy = [s for s in db_scenarios(app) if s.scenario_name == "Copy"][0]
    app.checkbox(key=f"fc_delok_{copy.id}").check().run()
    click(app, "Delete scenario")
    assert {s.scenario_name for s in db_scenarios(app)} == {"Default", "Alt renamed"}


def test_duplicate_names_are_refused(app):
    create_first(app)
    prop_id = str(db_scenarios(app)[0].property_id)
    app.text_input(key=f"fc_new_{prop_id}").set_value("default").run()
    click(app, "Create")
    assert any("already has" in e.value for e in app.error) and len(db_scenarios(app)) == 1


def test_delete_button_needs_the_confirmation_tick(app):
    create_first(app)
    assert [b for b in app.button if b.label == "Delete scenario"][0].disabled


# ---------------------------------------------------------------------------- the other tabs
def test_comparison_and_rules_tabs_render_without_errors(app):
    create_first(app)
    enter_mortgage(app)
    click(app, "Save scenario")
    assert not app.exception
    labels = [t.label for t in app.tabs]
    assert labels == ["Calculator", "Compare scenarios", "Compare properties", "Rules and sources"]
    assert any("1 properties, 1 with a current result" in c.value for c in app.caption)
    click(app, "Recalculate all scenarios")
    assert any("Recalculated 1 scenario" in s.value for s in app.success)


# ---------------------------------------------------------------------------- Property Master columns
def test_property_master_shows_primary_scenario_numbers(session):
    a = add(session, listing_number="C1", address="1 Alpha St", postal_code="M5V 2T6")
    b = add(session, listing_number="C2", address="2 Beta St", postal_code="M4E 1A1")
    add(session, listing_number="C3", address="3 Gamma St", postal_code="L5G 1A1")
    sc = sv.create_scenario(session, a, R)
    sv.apply_inputs(sc, dict(expected_purchase_price=D(500000), down_payment=D(25000), down_payment_pct=D(5),
                             mortgage_rate=D(5), mortgage_type="Fixed", amortization_years=25))
    sv.recalculate(session, sc, a, R)
    other = sv.create_scenario(session, b, R)                       # incomplete: no numbers
    sv.recalculate(session, other, b, R)
    session.commit()
    df = pm.build_frame([pm.property_row(p) for p in repo.list_properties(session)]).set_index("address")
    # listing tax 5,200 / 12 = 433.33; payment on 494,000 at 5% semi-annual over 25 years = 2,873.13
    assert df.loc["1 Alpha St", "monthly_outgoing"] == 2873.13 + 433.33
    # 25,000 down + 6,475 Ontario + 6,475 Toronto + 115.89 admin + 1,520 PST + 2,000 + 350 + 500
    assert df.loc["1 Alpha St", "cash_required"] == 42435.89
    assert df[["monthly_outgoing", "cash_required"]].loc["2 Beta St"].isna().all()
    assert df[["monthly_outgoing", "cash_required"]].loc["3 Gamma St"].isna().all()
    assert {"monthly_outgoing", "cash_required"} <= set(pm.DEFAULT_COLUMNS)


def test_only_the_primary_scenario_feeds_property_master(session):
    a = add(session)
    main = sv.create_scenario(session, a, R, "Main")
    alt = sv.create_scenario(session, a, R, "Alt")
    for s, rate in ((main, 5), (alt, 9)):
        sv.apply_inputs(s, dict(expected_purchase_price=D(500000), down_payment=D(25000), down_payment_pct=D(5),
                                mortgage_rate=D(rate), mortgage_type="Fixed", amortization_years=25))
        sv.recalculate(session, s, a, R)
    session.commit()
    row = pm.property_row(repo.list_properties(session)[0])
    assert row["monthly_outgoing"] == float(main.calculated["monthly"]["total"])
    sv.set_primary(session, alt)
    session.commit()
    assert pm.property_row(repo.list_properties(session)[0])["monthly_outgoing"] == float(alt.calculated["monthly"]["total"])


# ---------------------------------------------------------------------------- comparison export
def test_property_comparison_exports(session):
    import pandas as pd
    a = add(session)
    sc = sv.create_scenario(session, a, R)
    sv.apply_inputs(sc, dict(expected_purchase_price=D(500000), down_payment=D(25000), down_payment_pct=D(5),
                             mortgage_rate=D(5), mortgage_type="Fixed", amortization_years=25))
    sv.recalculate(session, sc, a, R)
    df = pd.DataFrame(sv.comparison_rows(repo.list_properties(session), R))
    kinds = ["text", "text"] + ["money"] * 6 + ["text"]
    ws = load_workbook(io.BytesIO(exp.build_table_xlsx(df, sv.COMPARISON_COLUMNS, kinds, "Property comparison")))["Property comparison"]
    assert [c.value for c in ws[1]] == sv.COMPARISON_COLUMNS
    assert ws["F2"].value == 2873.13 and ws["F2"].number_format == '"$"#,##0.00' and ws["I2"].value == "Current"
    csv = exp.build_table_csv(df, sv.COMPARISON_COLUMNS).decode("utf-8-sig")
    assert csv.splitlines()[0].startswith("Property,Scenario,Purchase price")