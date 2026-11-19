"""Unit tests for every formula in services/financial_service.py.

Expected values are hand-worked from the published rules, or computed by a separate float
implementation written here, never by calling the code under test.
"""
import json
from decimal import Decimal as D

import pytest

from services import financial_service as fs
from services.financial_rules import load_rules

R = load_rules()


def inputs(**kw) -> fs.ScenarioInputs:
    base = dict(price=D(500000), down_payment=D(25000), rate_pct=D(5), mortgage_type="Fixed", amortization_years=25)
    base.update(kw)
    return fs.ScenarioInputs(**base)


def calc(**kw):
    return fs.calculate(inputs(**kw), R)


def errors(**kw):
    out = calc(**kw)
    assert out["status"] == "blocked", out
    return " ".join(out["errors"])


# ---- independent float reference implementations -----------------------------------------
def ref_payment(principal, pct, comp, years):
    m = 2 if comp == "semi_annual" else 12
    r = (1 + pct / 100 / m) ** (m / 12) - 1
    n = years * 12
    return principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)


def ref_balance(principal, pct, comp, years, paid):
    m = 2 if comp == "semi_annual" else 12
    r = (1 + pct / 100 / m) ** (m / 12) - 1
    pmt = round(ref_payment(principal, pct, comp, years), 2)
    return principal * (1 + r) ** paid - pmt * ((1 + r) ** paid - 1) / r


# ============================================================================ monthly rate and payment
def test_monthly_rate_semi_annual_and_monthly():
    assert abs(float(fs.periodic_rate(D(5), "semi_annual")) - ((1.025) ** (1 / 6) - 1)) < 1e-12
    assert fs.periodic_rate(D(6), "monthly") == D("0.005")
    assert fs.periodic_rate(D(0), "semi_annual") == 0


@pytest.mark.parametrize("principal,pct,comp,years,expected", [
    (100000, 6, "monthly", 25, "644.30"),        # widely cited: $644.30 per $100,000
    (500000, 5, "semi_annual", 25, "2908.02"),   # widely cited
    (494000, 5, "semi_annual", 25, "2873.13"),
    (300000, 4.79, "monthly", 30, "1572.18"),
    (120000, 0, "monthly", 10, "1000.00"),       # zero rate: straight division
])
def test_monthly_payment_known_values(principal, pct, comp, years, expected):
    r = fs.periodic_rate(D(str(pct)), comp)
    assert fs.monthly_payment(D(principal), r, years * 12) == D(expected)


@pytest.mark.parametrize("pct", [1.5, 3.99, 5, 6.25, 9.9])
@pytest.mark.parametrize("comp", ["semi_annual", "monthly"])
@pytest.mark.parametrize("years", [10, 25, 30])
def test_payment_matches_independent_float_formula(pct, comp, years):
    r = fs.periodic_rate(D(str(pct)), comp)
    got = fs.monthly_payment(D(433500), r, years * 12)
    assert abs(float(got) - ref_payment(433500, pct, comp, years)) <= 0.005 + 1e-9


# ============================================================================ schedule, balance, interest
def test_schedule_repays_to_zero_and_accounting_is_exact():
    r = fs.periodic_rate(D(5), "semi_annual")
    pmt = fs.monthly_payment(D(494000), r, 300)
    s = fs.run_schedule(D(494000), r, pmt, 300)
    assert s["total_paid"] - s["total_interest"] == D(494000)           # every dollar paid is principal or interest
    assert abs(float(s["total_interest"]) - (300 * float(pmt) - 494000)) < 5  # last payment adjusts by a few dollars


def test_balance_and_interest_at_end_of_term_match_closed_form():
    r = fs.periodic_rate(D(5), "semi_annual")
    pmt = fs.monthly_payment(D(494000), r, 300)
    s = fs.run_schedule(D(494000), r, pmt, 300, checkpoint=60)
    assert abs(float(s["balance_at_checkpoint"]) - ref_balance(494000, 5, "semi_annual", 25, 60)) < 1.0
    principal_paid = 494000 - float(s["balance_at_checkpoint"])
    assert abs(float(s["interest_at_checkpoint"]) - (60 * float(pmt) - principal_paid)) < 0.5


def test_zero_rate_schedule():
    s = fs.run_schedule(D(120000), D(0), D(1000), 120, checkpoint=12)
    assert s["total_interest"] == 0 and s["balance_at_checkpoint"] == D(108000)


# ============================================================================ minimum down payment
@pytest.mark.parametrize("price,expected", [
    (400000, "20000.00"), (500000, "25000.00"), (600000, "35000.00"), (1000000, "75000.00"),
    (1499999, "124999.90"), (499999, "24999.95"),
])
def test_minimum_down_payment(price, expected):
    assert fs.minimum_down_payment(D(price), R.down_payment_minimum) == D(expected)


# ============================================================================ insurance premium
def premium(loan, price, amort=25, nt=False):
    return fs.premium_for(D(loan), D(price), amort, nt, R)


@pytest.mark.parametrize("loan,rate", [
    (65000, "0.60"), (65001, "1.70"), (75000, "1.70"), (75001, "2.40"), (80000, "2.40"), (80001, "2.80"),
    (85000, "2.80"), (85001, "3.10"), (90000, "3.10"), (90001, "4.00"), (95000, "4.00"),
])
def test_premium_tier_boundaries_on_a_100k_price(loan, rate):
    assert premium(loan, 100000)["base_rate_pct"] == D(rate)


def test_loan_to_value_above_95_has_no_premium():
    assert premium(95001, 100000)["base_rate_pct"] is None and premium(95001, 100000)["premium"] is None


def test_premium_amount_and_amortization_surcharge():
    assert premium(475000, 500000)["premium"] == D("19000.00")                     # 4.00% of 475,000
    p30 = premium(475000, 500000, amort=30)
    assert (p30["surcharge_pct"], p30["rate_pct"], p30["premium"]) == (D("0.20"), D("4.20"), D("19950.00"))
    assert premium(475000, 500000, amort=25)["surcharge_pct"] == 0                 # surcharge only beyond 25


def test_non_traditional_down_payment_tier():
    assert premium(460000, 500000, nt=True)["rate_pct"] == D("4.50")               # 92% LTV
    assert premium(460000, 500000, nt=True)["premium"] == D("20700.00")
    ordinary = premium(440000, 500000, nt=True)                                   # 88% LTV: tier does not apply
    assert ordinary["rate_pct"] == D("3.10") and ordinary["non_traditional_applied"] is False


# ============================================================================ land transfer tax
@pytest.mark.parametrize("price,expected", [
    (55000, "275.00"), (250000, "2225.00"), (400000, "4475.00"), (500000, "6475.00"), (700000, "10475.00"),
    (2000000, "36475.00"), (2500000, "48975.00"),
])
def test_ontario_ltt_hand_worked(price, expected):
    assert fs.land_transfer_tax(D(price), R.ontario_brackets)[0] == D(expected)


@pytest.mark.parametrize("price,expected", [
    (900000, "14475.00"), (3000000, "61475.00"), (4000000, "105475.00"), (5000000, "159975.00"),
    (10000000, "484975.00"), (20000000, "1239975.00"), (21000000, "1325975.00"),
])
def test_toronto_mltt_hand_worked_including_the_2026_luxury_tiers(price, expected):
    assert fs.land_transfer_tax(D(price), R.toronto_brackets)[0] == D(expected)


def test_ltt_working_rows_add_up_and_stop_at_the_price():
    total, rows = fs.land_transfer_tax(D(700000), R.ontario_brackets)
    assert [r["tax"] for r in rows] == [D("275.00"), D("1950.00"), D("2250.00"), D("6000.00")]
    assert sum(r["tax"] for r in rows) == total and rows[-1]["taxed"] == D("300000.00")


def test_ltt_rounds_half_up_to_the_cent():
    total, _ = fs.land_transfer_tax(D("55000.50"), R.ontario_brackets)   # 275 + 0.50 * 1%
    assert total == D("275.01")


def test_toronto_detection_by_city_name():
    assert fs.is_toronto_city("Toronto") and fs.is_toronto_city("  toronto ")
    assert not fs.is_toronto_city("Mississauga") and not fs.is_toronto_city(None)


# ============================================================================ full calculation
def test_worked_example_500k_five_percent_down():
    out = calc(first_time_buyer=False, in_toronto=False, legal_fees=D(2000), title_insurance=D(350), inspection_costs=D(500),
               other_closing_costs=D(0), annual_property_tax=D(4200), maintenance_fee=D(450), term_years=5)
    assert out["status"] == "ok" and out["errors"] == []
    m, mo, c, cash = out["mortgage"], out["monthly"], out["closing"], out["cash"]
    assert (m["base_mortgage"], m["premium"], m["total_mortgage"]) == ("475000.00", "19000.00", "494000.00")
    assert m["ltv_pct"] == "95.0000" and m["down_payment_pct"] == "5.00" and m["compounding"] == "semi_annual"
    assert m["monthly_payment"] == "2873.13" and m["minimum_down_payment"] == "25000.00"
    assert c["pst_on_premium"] == "1520.00" and c["ontario_ltt_net"] == "6475.00" and c["toronto_mltt_net"] == "0.00"
    # 6,475 + 1,520 + 2,000 + 350 + 500 = 10,845.00 closing; + 25,000 down
    assert c["total_closing_costs"] == "10845.00" and cash["total_cash_required"] == "35845.00"
    # 2,873.13 + 4,200/12 (350) + 450 = 3,673.13
    assert mo["property_tax"] == "350.00" and mo["total"] == "3673.13"


def test_first_time_buyer_toronto_all_lines():
    out = calc(price=D(900000), down_payment=D(75000), first_time_buyer=True, in_toronto=True, legal_fees=D(2000),
               title_insurance=D(350), inspection_costs=D(500), other_closing_costs=D(100), special_assessment=D(1000),
               amortization_years=30)
    c = out["closing"]
    # loan 825,000 at 91.67% LTV: 4.00% + 0.20% = 4.20% = 34,650; PST 8% = 2,772
    assert out["mortgage"]["premium"] == "34650.00" and c["pst_on_premium"] == "2772.00"
    assert (c["ontario_ltt_gross"], c["ontario_ftb_rebate"], c["ontario_ltt_net"]) == ("14475.00", "4000.00", "10475.00")
    assert (c["toronto_mltt_gross"], c["toronto_ftb_rebate"], c["toronto_mltt_net"]) == ("14475.00", "4475.00", "10000.00")
    assert c["toronto_admin_fee_with_hst"] == "115.89"                       # 102.56 + 13% HST
    assert c["total_closing_costs"] == "26312.89"                           # 10475+10000+115.89+2772+2000+350+500+100
    assert out["cash"]["total_cash_required"] == "102312.89"                # 75,000 + 26,312.89 + 1,000 assessment
    assert out["cash"]["special_assessment"] == "1000.00"                   # one-time: in cash, never in the monthly figure


@pytest.mark.parametrize("price,ontario_net", [(300000, "0.00"), (368000, "0.00"), (369000, "10.00"), (700000, "6475.00")])
def test_ontario_ftb_rebate_covers_the_full_tax_up_to_368k(price, ontario_net):
    out = calc(price=D(price), down_payment=D(price) * D("0.1") + 25000, first_time_buyer=True)
    assert out["closing"]["ontario_ltt_net"] == ontario_net


def test_toronto_rebate_covers_full_tax_up_to_400k():
    full = calc(price=D(400000), down_payment=D(40000), first_time_buyer=True, in_toronto=True)["closing"]
    assert full["toronto_mltt_gross"] == "4475.00" and full["toronto_mltt_net"] == "0.00"
    assert calc(price=D(400000), down_payment=D(40000), first_time_buyer=False, in_toronto=True)["closing"]["toronto_mltt_net"] == "4475.00"


def test_toronto_lines_are_zero_outside_toronto():
    c = calc(in_toronto=False)["closing"]
    assert c["toronto_mltt_gross"] == "0.00" and c["toronto_admin_fee_with_hst"] == "0.00" and c["toronto_brackets"] == []


def test_monthly_total_is_the_sum_of_every_line():
    out = calc(annual_property_tax=D(6000), maintenance_fee=D("512.40"), electricity=D(80), natural_gas=D(60), water=D(45),
               home_insurance=D(120), internet=D(70), water_heater_rental=D("32.50"), repair_reserve=D(200))
    mo = out["monthly"]
    expected = D(mo["mortgage_payment"]) + D(500) + D("512.40") + 80 + 60 + 45 + 120 + 70 + D("32.50") + 200
    assert D(mo["total"]) == expected and mo["property_tax"] == "500.00"


def test_blank_costs_count_as_zero_and_are_listed_as_not_entered():
    out = calc()
    assert out["monthly"]["electricity"] == "0.00" and "Electricity" in out["not_entered"]
    assert "Legal fees" in out["not_entered"] and out["cash"]["special_assessment"] == "0.00"
    full = calc(**{a: D(1) for a, _ in fs.OPTIONAL_COSTS})
    assert full["not_entered"] == []


def test_variable_rate_defaults_to_monthly_compounding_and_override_wins():
    assert calc(mortgage_type="Variable")["mortgage"]["compounding"] == "monthly"
    assert calc(mortgage_type="Fixed")["mortgage"]["compounding"] == "semi_annual"
    over = calc(mortgage_type="Fixed", compounding="monthly")["mortgage"]
    assert over["compounding"] == "monthly"
    r = fs.periodic_rate(D(5), "monthly")
    assert over["monthly_payment"] == str(fs.monthly_payment(D(494000), r, 300))


def test_term_outputs_only_when_a_term_is_given():
    assert "balance_at_term_end" not in calc()["mortgage"]
    m = calc(term_years=5)["mortgage"]
    assert abs(float(m["balance_at_term_end"]) - ref_balance(494000, 5, "semi_annual", 25, 60)) < 1.0
    assert D(m["principal_paid_in_term"]) + D(m["balance_at_term_end"]) == D("494000.00")
    assert m["term_years"] == 5


@pytest.mark.parametrize("rate,expected", [("5", "7"), ("3", "5.25"), ("3.25", "5.25"), ("3.5", "5.5"), ("6.99", "8.99")])
def test_stress_test_qualifying_rate_is_greater_of_plus_two_or_floor(rate, expected):
    m = calc(rate_pct=D(rate))["mortgage"]
    assert D(m["stress_qualifying_rate_pct"]) == D(expected)
    assert D(m["stress_qualifying_payment"]) >= D(m["monthly_payment"])
    r = fs.periodic_rate(D(expected), "semi_annual")
    assert m["stress_qualifying_payment"] == str(fs.monthly_payment(D(494000), r, 300))


def test_result_is_plain_json_and_carries_rules_and_input_fingerprints():
    out = calc(term_years=5, in_toronto=True)
    assert json.loads(json.dumps(out)) == out
    assert out["rules"] == {"version": R.version, "hash": R.rules_hash}
    assert out["inputs_hash"] == fs.inputs_hash(inputs(term_years=5, in_toronto=True))
    assert fs.inputs_hash(inputs()) != fs.inputs_hash(inputs(rate_pct=D("5.1")))


def test_fingerprint_ignores_how_a_number_is_written():
    a = fs.inputs_hash(inputs(price=D("500000"), down_payment=D("25000")))
    assert a == fs.inputs_hash(inputs(price=D("500000.00"), down_payment=D("25000.0")))
    assert a == fs.inputs_hash(inputs(price=D("5E+5"), down_payment=D("25000.000")))
    assert a != fs.inputs_hash(inputs(price=D("500000.01")))


def test_result_amount_reads_ok_results_only():
    ok = calc()
    assert fs.result_amount(ok, "monthly", "total") == D(ok["monthly"]["total"])
    assert fs.result_amount(None, "monthly", "total") is None
    assert fs.result_amount(calc(down_payment=D(1)), "monthly", "total") is None


# ============================================================================ blocked and incomplete
def test_missing_required_inputs_are_named_not_guessed():
    out = fs.calculate(fs.ScenarioInputs(price=D(500000)), R)
    assert out["status"] == "incomplete"
    assert out["errors"] == ["Enter down payment, mortgage rate, mortgage type, amortization to calculate."]
    assert "mortgage" not in out


def test_down_payment_below_minimum_is_blocked():
    msg = errors(down_payment=D(20000))
    assert "minimum down payment for a $500,000 home is $25,000" in msg and "You entered $20,000" in msg
    assert "$35,000" in errors(price=D(600000), down_payment=D(34999))


def test_twenty_percent_down_or_more_is_out_of_scope():
    assert "does not need mortgage default insurance" in errors(down_payment=D(100000))
    assert "does not need" in errors(down_payment=D(250000))


def test_price_at_or_above_the_insurable_limit_is_blocked():
    assert "only available below $1,500,000" in errors(price=D(1500000), down_payment=D(300000))
    assert "only available below" in errors(price=D(2000000), down_payment=D(100000))
    ok = calc(price=D(1499999), down_payment=D("149999.90"), amortization_years=25)
    assert ok["status"] == "ok" and ok["mortgage"]["minimum_down_payment"] == "124999.90"


def test_amortization_limits_depend_on_first_time_buyer():
    assert "first-time buyer" in errors(amortization_years=30)
    ok = calc(amortization_years=30, first_time_buyer=True)
    assert ok["status"] == "ok" and ok["mortgage"]["premium_surcharge_pct"] == "0.20"
    assert ok["mortgage"]["premium"] == "19950.00"
    assert "between 1 and 30" in errors(amortization_years=31, first_time_buyer=True)
    assert "between 1 and 30" in errors(amortization_years=0, first_time_buyer=True)
    assert "between 1 and 30" in errors(amortization_years=35)
    assert calc(amortization_years=25)["mortgage"]["premium_surcharge_pct"] == "0.00"


def test_term_cannot_exceed_amortization_or_be_zero():
    assert "Term must be" in errors(term_years=26)
    assert "Term must be" in errors(term_years=0)
    assert calc(term_years=25)["status"] == "ok"


@pytest.mark.parametrize("kw,text", [
    ({"price": D(0)}, "more than $0"), ({"rate_pct": D(101)}, "between 0% and 100%"), ({"rate_pct": D(-1)}, "between 0% and 100%"),
    ({"down_payment": D(500000)}, "less than the purchase price"), ({"down_payment": D(-5)}, "at least $0"),
    ({"mortgage_type": "Weird"}, "Mortgage type must be"), ({"compounding": "daily"}, "Compounding must be"),
])
def test_basic_input_errors(kw, text):
    assert text in errors(**kw)


def test_non_traditional_flag_outside_its_range_warns():
    out = calc(non_traditional=True, price=D(500000), down_payment=D(60000))
    assert out["status"] == "ok" and any("non-traditional" in w for w in out["warnings"])


def test_first_time_buyer_results_carry_an_eligibility_warning():
    assert any("assume you qualify" in w for w in calc(first_time_buyer=True)["warnings"])
    assert not calc(first_time_buyer=False)["warnings"]


def test_changing_a_rule_changes_the_result_proving_nothing_is_hard_coded():
    from services.financial_rules import DEFAULT_PATH, parse_rules
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    other = parse_rules(text.replace("pst_on_premium_pct = 8.0", "pst_on_premium_pct = 10.0").replace("rate_pct = 4.00", "rate_pct = 5.00"))
    out = fs.calculate(inputs(), other)
    assert out["mortgage"]["premium"] == "23750.00" and out["closing"]["pst_on_premium"] == "2375.00"