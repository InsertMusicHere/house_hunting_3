"""Financial scenarios: prefill, names, primary flag, input resolution, saved results and stale detection."""
from decimal import Decimal as D

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import repository as repo
from database.models import FinancialDetails
from services import scenarios as sv
from services.financial_rules import DEFAULT_PATH, load_rules, parse_rules
from tests.master_helpers import add

R = load_rules()


def filled(session, prop, name="Default", **over):
    """A complete, valid scenario for prop (insured, 5% down, 25 years)."""
    sc = sv.create_scenario(session, prop, R, name)
    vals = dict(expected_purchase_price=D(500000), down_payment=D(25000), down_payment_pct=D(5), mortgage_rate=D("5.000"),
                mortgage_type="Fixed", amortization_years=25)
    vals.update(over)
    sv.apply_inputs(sc, vals)
    sv.recalculate(session, sc, prop, R)
    session.commit()
    return sc


# ---------------------------------------------------------------------------- creation and prefill
def test_new_scenario_is_prefilled_from_listing_and_personal_defaults_only(session):
    p = add(session, asking_price="$950,000", maintenance_fee="410.25", listed_annual_property_tax="5,200", city="Toronto")
    sc = sv.create_scenario(session, p, R)
    assert sc.scenario_name == "Default" and sc.is_primary
    assert sc.expected_purchase_price == D("950000.00") and sc.monthly_maintenance_fee == D("410.25")
    assert sc.annual_property_tax == D("5200.00")
    assert (sc.legal_fees, sc.title_insurance, sc.inspection_costs, sc.other_closing_costs) == (D(2000), D(350), D(500), D(0))
    assert sc.first_time_buyer is False and sc.toronto_override is None
    # nothing else is guessed
    assert sc.mortgage_rate is None and sc.mortgage_type is None and sc.amortization_years is None
    assert sc.down_payment is None and sc.electricity is None and sc.calculated is None


def test_missing_listing_values_stay_blank(session):
    p = add(session, asking_price=None, maintenance_fee=None, listed_annual_property_tax=None)
    sc = sv.create_scenario(session, p, R)
    assert sc.expected_purchase_price is None and sc.monthly_maintenance_fee is None and sc.annual_property_tax is None


def test_only_the_first_scenario_becomes_primary(session):
    p = add(session)
    a, b = sv.create_scenario(session, p, R, "A"), sv.create_scenario(session, p, R, "B")
    assert (a.is_primary, b.is_primary) == (True, False)
    assert [s.scenario_name for s in repo.scenarios_for_property(session, p.id)] == ["A", "B"]


# ---------------------------------------------------------------------------- names
@pytest.mark.parametrize("name,fragment", [("", "Give the scenario a name"), ("   ", "Give the scenario a name"),
                                           ("x" * 101, "limit is 100"), ("default", "already has")])
def test_bad_names_are_refused(session, name, fragment):
    p = add(session)
    sv.create_scenario(session, p, R, "Default")
    with pytest.raises(ValueError, match=fragment):
        sv.create_scenario(session, p, R, name)


def test_same_name_is_fine_on_a_different_property(session):
    a, b = add(session, listing_number="C1"), add(session, listing_number="C2", address="2 Other St", postal_code="M4E 1A1")
    sv.create_scenario(session, a, R, "Default")
    assert sv.create_scenario(session, b, R, "Default").scenario_name == "Default"


def test_rename_and_its_checks(session):
    p = add(session)
    a, b = sv.create_scenario(session, p, R, "A"), sv.create_scenario(session, p, R, "B")
    sv.rename_scenario(session, a, "  A renamed ")
    assert a.scenario_name == "A renamed"
    sv.rename_scenario(session, a, "a renamed")           # same scenario, new case: allowed
    with pytest.raises(ValueError, match="already has"):
        sv.rename_scenario(session, b, "A RENAMED")


# ---------------------------------------------------------------------------- duplicate, delete, primary
def test_duplicate_copies_inputs_not_primary_and_calculates(session):
    p = add(session)
    sc = filled(session, p, special_assessment=D(1500))
    copy = sv.duplicate_scenario(session, sc, "Copy", R)
    session.commit()
    assert copy.id != sc.id and not copy.is_primary and sc.is_primary
    assert copy.special_assessment == D(1500) and copy.mortgage_rate == D("5.000")
    assert copy.calculated["status"] == "ok" and copy.calculated["cash"]["special_assessment"] == "1500.00"
    with pytest.raises(ValueError):
        sv.duplicate_scenario(session, sc, "copy", R)


def test_set_primary_moves_the_flag(session):
    p = add(session)
    a, b = sv.create_scenario(session, p, R, "A"), sv.create_scenario(session, p, R, "B")
    sv.set_primary(session, b)
    session.commit()
    assert (a.is_primary, b.is_primary) == (False, True) and sv.primary_of(p) is b


def test_database_refuses_two_primary_scenarios(session):
    p = add(session)
    sv.create_scenario(session, p, R, "A")
    other = FinancialDetails(property_id=p.id, scenario_name="B", is_primary=True)
    session.add(other)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_delete_reports_primary_and_does_not_promote_another(session):
    p = add(session)
    a, b = sv.create_scenario(session, p, R, "A"), sv.create_scenario(session, p, R, "B")
    assert sv.delete_scenario(session, a) is True
    session.commit()
    assert sv.primary_of(p) is None and [s.scenario_name for s in repo.scenarios_for_property(session, p.id)] == ["B"]
    assert sv.delete_scenario(session, b) is False


def test_deleting_the_property_deletes_its_scenarios(session):
    p = add(session)
    sv.create_scenario(session, p, R)
    session.commit()
    repo.delete_property(session, p)
    session.commit()
    assert session.scalars(select(FinancialDetails)).all() == []


def test_apply_inputs_rejects_unknown_fields(session):
    sc = sv.create_scenario(session, add(session), R)
    with pytest.raises(KeyError):
        sv.apply_inputs(sc, {"calculated": {}})


# ---------------------------------------------------------------------------- input resolution
@pytest.mark.parametrize("city,override,expected", [
    ("Toronto", None, True), (" toronto ", None, True), ("Mississauga", None, False),
    ("Mississauga", True, True), ("Toronto", False, False)])
def test_toronto_is_detected_from_the_city_and_can_be_overridden(session, city, override, expected):
    p = add(session, city=city)
    sc = sv.create_scenario(session, p, R)
    sc.toronto_override = override
    assert sv.resolve_inputs(sc, p, R).in_toronto is expected


def test_first_time_buyer_null_uses_the_config_and_explicit_value_wins(session):
    p = add(session)
    sc = sv.create_scenario(session, p, R)
    sc.first_time_buyer = None
    assert sv.resolve_inputs(sc, p, R).first_time_buyer is R.personal.first_time_buyer
    sc.first_time_buyer = True
    assert sv.resolve_inputs(sc, p, R).first_time_buyer is True


def test_down_payment_follows_whichever_of_dollars_or_percent_was_edited_last(session):
    p = add(session)
    sc = sv.create_scenario(session, p, R)
    sv.apply_inputs(sc, dict(expected_purchase_price=D(600000), down_payment=D(30000), down_payment_pct=D("7.50"),
                             down_payment_basis="percent"))
    assert sv.resolve_inputs(sc, p, R).down_payment == D("45000.00")      # percent wins
    sv.apply_inputs(sc, dict(down_payment_basis="amount"))
    assert sv.resolve_inputs(sc, p, R).down_payment == D(30000)           # dollars win
    sv.apply_inputs(sc, dict(down_payment=None, down_payment_basis=None))
    assert sv.resolve_inputs(sc, p, R).down_payment == D("45000.00")      # only a percent is known


# ---------------------------------------------------------------------------- saved results
def test_recalculate_saves_result_time_and_asking_price(session):
    p = add(session, asking_price="$950,000")
    sc = filled(session, p)
    assert sc.calculated["status"] == "ok" and sc.calculated_at is not None
    assert sc.calculated["asking_price_at_calc"] == "950000.00"
    assert sc.calculated["mortgage"]["monthly_payment"] == "2873.13"


def test_incomplete_and_blocked_results_are_saved_too(session):
    p = add(session)
    sc = sv.create_scenario(session, p, R)
    assert sv.recalculate(session, sc, p, R)["status"] == "incomplete"
    sv.apply_inputs(sc, dict(down_payment=D(1000), mortgage_rate=D(5), mortgage_type="Fixed", amortization_years=25))
    assert sv.recalculate(session, sc, p, R)["status"] == "blocked" and sc.calculated["status"] == "blocked"


def test_calculate_only_saves_nothing(session):
    p = add(session)
    sc = filled(session, p)
    before = sc.calculated_at
    sv.apply_inputs(sc, dict(mortgage_rate=D(6)))
    out = sv.calculate_only(sc, p, R)
    assert out["mortgage"]["rate_pct"] == "6.000" and sc.calculated["mortgage"]["rate_pct"] == "5.000"
    assert sc.calculated_at == before


# ---------------------------------------------------------------------------- stale detection
def test_a_fresh_result_is_current(session):
    p = add(session)
    assert sv.staleness(filled(session, p), p, R) == []


def test_never_calculated_is_flagged(session):
    p = add(session)
    assert sv.staleness(sv.create_scenario(session, p, R), p, R) == ["Not calculated yet."]


def test_changed_inputs_are_flagged(session):
    p = add(session)
    sc = filled(session, p)
    sc.mortgage_rate = D("5.250")
    assert "inputs" in sv.staleness(sc, p, R)[0]


def test_changed_toronto_detection_or_first_time_setting_is_flagged(session):
    p = add(session, city="Mississauga")
    sc = filled(session, p)
    p.city = "Toronto"
    assert any("Toronto detection" in r for r in sv.staleness(sc, p, R))
    p.city = "Mississauga"
    sc.first_time_buyer = True
    assert sv.staleness(sc, p, R)


def test_changed_asking_price_is_flagged_with_both_prices(session):
    p = add(session, asking_price="$950,000")
    sc = filled(session, p)
    p.asking_price = D("925000")
    reasons = sv.staleness(sc, p, R)
    assert reasons == ["The asking price changed from $950,000 to $925,000 since this was calculated."]


def test_changed_rules_are_flagged_but_personal_edits_are_not(session):
    p = add(session)
    sc = filled(session, p)
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    assert sv.staleness(sc, p, parse_rules(text.replace("legal_fees = 2000", "legal_fees = 3000"))) == []
    changed = parse_rules(text.replace("pst_on_premium_pct = 8.0", "pst_on_premium_pct = 9.0"))
    reasons = sv.staleness(sc, p, changed)
    assert len(reasons) == 1 and "rules file changed" in reasons[0]


def test_recalculate_all_refreshes_every_scenario(session):
    a, b = add(session, listing_number="C1"), add(session, listing_number="C2", address="2 Other St", postal_code="M4E 1A1")
    s1 = filled(session, a)
    filled(session, b, "Alt")
    s1.mortgage_rate = D("6.000")
    assert sv.recalculate_all(session, R) == 2
    assert s1.calculated["mortgage"]["rate_pct"] == "6.000" and not sv.staleness(s1, a, R)


# ---------------------------------------------------------------------------- comparison across properties
def test_comparison_uses_the_primary_scenario_and_reports_status(session):
    a = add(session, listing_number="C1", address="1 Alpha St", postal_code="M5V 2T6")
    b = add(session, listing_number="C2", address="2 Beta St", postal_code="M4E 1A1")
    c = add(session, listing_number="C3", address="3 Gamma St", postal_code="L5G 1A1")
    d = add(session, listing_number="C4", address="4 Delta St", postal_code="L5G 1A2")
    filled(session, a, "Main")
    alt = filled(session, a, "Alt", mortgage_rate=D("7.000"))        # not primary: ignored
    blocked = sv.create_scenario(session, b, R)
    sv.recalculate(session, blocked, b, R)                            # incomplete
    sv.create_scenario(session, c, R)                                 # never calculated
    sc_stale = filled(session, d)
    sc_stale.mortgage_rate = D("6")                                   # edited after calculating
    rows = {r["Property"].split(" - ")[0]: r for r in sv.comparison_rows(repo.list_properties(session), R)}
    assert rows["1 Alpha St"]["Scenario"] == "Main" and rows["1 Alpha St"]["Status"] == "Current"
    assert rows["1 Alpha St"]["Monthly payment"] == 2873.13 and rows["1 Alpha St"]["Cash required"] > 25000
    assert rows["2 Beta St"]["Status"] == "Incomplete" and rows["2 Beta St"]["Monthly payment"] is None
    assert rows["3 Gamma St"]["Status"] == "Not calculated"
    assert rows["4 Delta St"]["Status"] == "Out of date" and alt.calculated["mortgage"]["rate_pct"] == "7.000"


def test_properties_without_a_primary_scenario_still_appear(session):
    add(session)
    row = sv.comparison_rows(repo.list_properties(session), R)[0]
    assert row["Status"] == "No primary scenario" and row["Total monthly outgoing"] is None