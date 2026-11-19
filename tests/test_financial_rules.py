"""config/financial_rules.toml: contents, source citations, validation and change detection."""
import os
import tomllib
from decimal import Decimal as D

import pytest

from services.financial_rules import DEFAULT_PATH, RulesError, load_rules, parse_rules

TEXT = DEFAULT_PATH.read_text(encoding="utf-8")


def variant(old: str, new: str) -> str:
    assert old in TEXT, f"test setup: {old!r} not in the rules file"
    return TEXT.replace(old, new, 1)


# ---------------------------------------------------------------------------- the real file
def test_real_file_has_the_verified_values():
    r = load_rules()
    assert [(t.max_ltv_pct, t.rate_pct) for t in r.premium_tiers] == [
        (D(65), D("0.60")), (D(75), D("1.70")), (D(80), D("2.40")), (D(85), D("2.80")), (D(90), D("3.10")), (D(95), D("4.00"))]
    assert (r.extended_amortization_over_years, r.extended_amortization_surcharge_pct) == (25, D("0.20"))
    assert r.non_traditional_rate_pct == D("4.50") and r.non_traditional_min_ltv_pct == D(90)
    assert (r.max_insurable_price, r.max_ltv_pct, r.uninsured_down_payment_pct) == (D(1500000), D(95), D(20))
    assert (r.max_amortization_years, r.max_amortization_years_ftb) == (25, 30)
    assert r.pst_on_premium_pct == D(8) and (r.stress_buffer_pct, r.stress_floor_pct) == (D(2), D("5.25"))
    assert [(b.up_to, b.rate_pct) for b in r.down_payment_minimum] == [(D(500000), D(5)), (D(1500000), D(10))]
    assert [(b.up_to, b.rate_pct) for b in r.ontario_brackets] == [
        (D(55000), D("0.5")), (D(250000), D(1)), (D(400000), D("1.5")), (D(2000000), D(2)), (None, D("2.5"))]
    assert [b.rate_pct for b in r.toronto_brackets][-5:] == [D("4.4"), D("5.45"), D("6.5"), D("7.55"), D("8.6")]
    assert (r.ontario_rebate_max, r.toronto_rebate_max) == (D(4000), D(4475))
    assert (r.toronto_admin_fee, r.toronto_hst_pct) == (D("102.56"), D(13))
    assert r.default_compounding == {"Fixed": "semi_annual", "Variable": "monthly"}


def test_personal_defaults_are_the_agreed_estimates_and_not_assumed_first_time():
    p = load_rules().personal
    assert (p.legal_fees, p.title_insurance, p.inspection_costs, p.other_closing_costs) == (D(2000), D(350), D(500), D(0))
    assert p.first_time_buyer is False  # the owner must choose; the cautious value is the default


def test_every_cited_source_exists_and_has_a_confidence_note():
    raw = tomllib.loads(TEXT)
    ids = {s["id"] for s in raw["sources"]}
    cited = []
    for section in (raw["stress_test"], raw["mortgage"], raw["insurance"], raw["land_transfer"]["ontario"], raw["land_transfer"]["toronto"]):
        cited += section["sources"]
    assert cited and set(cited) <= ids
    assert all(s.get("confidence") and s.get("label") for s in raw["sources"])
    assert load_rules().sources and len(load_rules().sources) == len(raw["sources"])


def test_source_urls_are_only_pages_seen_in_research():
    raw = tomllib.loads(TEXT)
    for s in raw["sources"]:
        assert s["url"] == "" or s["url"].startswith("https://")


# ---------------------------------------------------------------------------- hash and reload
def test_hash_ignores_personal_sources_and_meta_but_not_rules():
    base = parse_rules(TEXT).rules_hash
    assert parse_rules(variant("legal_fees = 2000", "legal_fees = 2500")).rules_hash == base
    assert parse_rules(variant("first_time_buyer = false", "first_time_buyer = true")).rules_hash == base
    assert parse_rules(variant('version = "2026-11-16"', 'version = "2027-01-01"')).rules_hash == base
    assert parse_rules(variant("pst_on_premium_pct = 8.0", "pst_on_premium_pct = 9.0")).rules_hash != base
    assert parse_rules(variant("rate_pct = 4.00", "rate_pct = 4.10")).rules_hash != base


def test_edits_to_the_file_are_picked_up_without_restart(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text(TEXT, encoding="utf-8")
    first = load_rules(path)
    path.write_text(variant("pst_on_premium_pct = 8.0", "pst_on_premium_pct = 9.0"), encoding="utf-8")
    os.utime(path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    assert load_rules(path).pst_on_premium_pct == D(9) and first.pst_on_premium_pct == D(8)


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(RulesError, match="not found"):
        load_rules(tmp_path / "nope.toml")


# ---------------------------------------------------------------------------- validation
@pytest.mark.parametrize("old,new,message", [
    ("[stress_test]", "[stress_testx]", r"Missing \[stress_test\]"),
    ("first_time_buyer = false", 'first_time_buyer = "no"', "true or false"),
    ("buffer_pct = 2.00", "buffer_pct = 250", "not between 0 and 100"),
    ("legal_fees = 2000", "legal_fees = -5", "cannot be negative"),
    ("max_insurable_price = 1500000", "max_insurable_price = 1400000", "must equal"),
    ("max_amortization_years = 25 ", "max_amortization_years = 35 ", "cannot be shorter"),
    ('Variable = "monthly"', 'Variable = "weekly"', "semi_annual"),
    ("pst_on_premium_pct = 8.0", 'pst_on_premium_pct = "eight"', "not a number"),
])
def test_bad_values_are_rejected_with_a_clear_message(old, new, message):
    with pytest.raises(RulesError, match=message):
        parse_rules(variant(old, new))


def test_not_toml_is_rejected():
    with pytest.raises(RulesError, match="not valid TOML"):
        parse_rules("this = = broken")


def test_tiers_must_increase_and_end_at_max_ltv():
    with pytest.raises(RulesError, match="must increase"):
        parse_rules(variant("max_ltv_pct = 75\nrate_pct = 1.70", "max_ltv_pct = 60\nrate_pct = 1.70"))
    with pytest.raises(RulesError, match="must match"):
        parse_rules(variant("max_ltv_pct = 95\nrate_pct = 4.00", "max_ltv_pct = 92\nrate_pct = 4.00"))


def test_brackets_must_ascend_and_the_last_must_be_open():
    with pytest.raises(RulesError, match="larger than the previous"):
        parse_rules(variant("up_to = 250000\nrate_pct = 1.0", "up_to = 50000\nrate_pct = 1.0"))
    with pytest.raises(RulesError, match="no up_to"):
        parse_rules(variant("[[land_transfer.ontario.brackets]]\nrate_pct = 2.5",
                            "[[land_transfer.ontario.brackets]]\nup_to = 9000000\nrate_pct = 2.5"))