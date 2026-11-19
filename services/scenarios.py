"""Financial scenarios: create, copy, rename, delete, mark primary, calculate and detect stale results.

A property can have several named scenarios (rows of financial_details). At most one is the
"primary" scenario, which Property Master and the property comparison use. No page code here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from database import repository as repo
from database.models import FinancialDetails, Property
from services import financial_service as fs
from services.financial_rules import Rules

# Columns the calculator page edits (everything on financial_details except ids, names and results).
INPUT_FIELDS = [
    "expected_purchase_price", "down_payment", "down_payment_pct", "down_payment_basis", "mortgage_rate",
    "mortgage_type", "amortization_years", "term_years", "compounding", "first_time_buyer",
    "toronto_override", "non_traditional_down_payment", "annual_property_tax", "monthly_maintenance_fee",
    "electricity", "natural_gas", "water", "home_insurance", "internet", "water_heater_rental",
    "repair_reserve", "special_assessment", "legal_fees", "title_insurance", "inspection_costs",
    "other_closing_costs",
]
NAME_LIMIT = 100


def validate_name(session: Session, prop: Property, name: str, exclude_id=None) -> str | None:
    """Return a problem description, or None if the name can be used."""
    name = (name or "").strip()
    if not name:
        return "Give the scenario a name."
    if len(name) > NAME_LIMIT:
        return f"The name is {len(name)} characters; the limit is {NAME_LIMIT}."
    for other in repo.scenarios_for_property(session, prop.id):
        if other.id != exclude_id and other.scenario_name.casefold() == name.casefold():
            return f"This property already has a scenario named {other.scenario_name!r}."
    return None


def create_scenario(session: Session, prop: Property, rules: Rules, name: str = "Default") -> FinancialDetails:
    """New scenario pre-filled from the listing and your personal defaults. Nothing else is guessed:
    rate, mortgage type, amortization, down payment and the utilities stay blank until you enter them."""
    problem = validate_name(session, prop, name)
    if problem:
        raise ValueError(problem)
    first = not repo.scenarios_for_property(session, prop.id)
    p = rules.personal
    sc = FinancialDetails(
        property_id=prop.id, scenario_name=name.strip(), is_primary=first,
        expected_purchase_price=prop.asking_price, annual_property_tax=prop.listed_annual_property_tax,
        monthly_maintenance_fee=prop.maintenance_fee, first_time_buyer=p.first_time_buyer,
        legal_fees=p.legal_fees, title_insurance=p.title_insurance, inspection_costs=p.inspection_costs,
        other_closing_costs=p.other_closing_costs, non_traditional_down_payment=False,
    )
    # A new scenario inherits saved monthly estimates from the property's
    # primary (or oldest available) scenario, without sharing mortgage inputs.
    existing = repo.scenarios_for_property(session, prop.id)
    if existing:
        source = existing[0]
        for field in (
            "electricity", "natural_gas", "water", "home_insurance", "internet",
            "water_heater_rental", "repair_reserve",
        ):
            setattr(sc, field, getattr(source, field))
    prop.financials.append(sc)
    session.flush()
    return sc


def duplicate_scenario(session: Session, sc: FinancialDetails, name: str, rules: Rules) -> FinancialDetails:
    problem = validate_name(session, sc.property, name)
    if problem:
        raise ValueError(problem)
    copy = FinancialDetails(property_id=sc.property_id, scenario_name=name.strip(), is_primary=False)
    for field in INPUT_FIELDS:
        setattr(copy, field, getattr(sc, field))
    sc.property.financials.append(copy)
    session.flush()
    recalculate(session, copy, sc.property, rules)
    return copy


def rename_scenario(session: Session, sc: FinancialDetails, name: str) -> None:
    problem = validate_name(session, sc.property, name, exclude_id=sc.id)
    if problem:
        raise ValueError(problem)
    sc.scenario_name = name.strip()
    session.flush()


def delete_scenario(session: Session, sc: FinancialDetails) -> bool:
    """Delete one scenario. Returns True if it was the primary one (no other becomes primary automatically)."""
    was_primary = sc.is_primary
    session.delete(sc)
    session.flush()
    return was_primary


def set_primary(session: Session, sc: FinancialDetails) -> None:
    for other in repo.scenarios_for_property(session, sc.property_id):
        if other.id != sc.id and other.is_primary:
            other.is_primary = False
    session.flush()  # clear the old one first: the database allows only one primary per property
    sc.is_primary = True
    session.flush()


def primary_of(prop: Property) -> FinancialDetails | None:
    return next((f for f in prop.financials if f.is_primary), None)


def apply_inputs(sc: FinancialDetails, values: dict[str, Any]) -> None:
    unknown = set(values) - set(INPUT_FIELDS)
    if unknown:
        raise KeyError(f"Not scenario inputs: {sorted(unknown)}")
    for key, value in values.items():
        setattr(sc, key, value)


# --------------------------------------------------------------------------- calculation
def _resolved_down_payment(sc: FinancialDetails, price: Decimal | None) -> Decimal | None:
    pct, amount = sc.down_payment_pct, sc.down_payment
    if price is not None and pct is not None and (sc.down_payment_basis == "percent" or amount is None):
        return (price * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return amount


def resolve_inputs(sc: FinancialDetails, prop: Property, rules: Rules) -> fs.ScenarioInputs:
    """Turn a scenario row into calculator inputs, filling the three "use the default" flags."""
    price = sc.expected_purchase_price
    return fs.ScenarioInputs(
        price=price, down_payment=_resolved_down_payment(sc, price), rate_pct=sc.mortgage_rate,
        mortgage_type=sc.mortgage_type, amortization_years=sc.amortization_years, term_years=sc.term_years,
        compounding=sc.compounding,
        first_time_buyer=rules.personal.first_time_buyer if sc.first_time_buyer is None else sc.first_time_buyer,
        non_traditional=bool(sc.non_traditional_down_payment),
        in_toronto=fs.is_toronto_city(prop.city) if sc.toronto_override is None else sc.toronto_override,
        annual_property_tax=sc.annual_property_tax, maintenance_fee=sc.monthly_maintenance_fee,
        electricity=sc.electricity, natural_gas=sc.natural_gas, water=sc.water, home_insurance=sc.home_insurance,
        internet=sc.internet, water_heater_rental=sc.water_heater_rental, repair_reserve=sc.repair_reserve,
        special_assessment=sc.special_assessment, legal_fees=sc.legal_fees, title_insurance=sc.title_insurance,
        inspection_costs=sc.inspection_costs, other_closing_costs=sc.other_closing_costs,
    )


def calculate_only(sc: FinancialDetails, prop: Property, rules: Rules) -> dict[str, Any]:
    """Calculate without saving anything."""
    result = fs.calculate(resolve_inputs(sc, prop, rules), rules)
    result["asking_price_at_calc"] = None if prop.asking_price is None else str(prop.asking_price)
    return result


def recalculate(session: Session, sc: FinancialDetails, prop: Property, rules: Rules) -> dict[str, Any]:
    """Calculate and save the result (and when) on the scenario."""
    result = calculate_only(sc, prop, rules)
    sc.calculated = result
    sc.calculated_at = datetime.now(timezone.utc)
    session.flush()
    return result


def recalculate_all(session: Session, rules: Rules) -> int:
    scenarios = repo.all_scenarios(session)
    for sc in scenarios:
        recalculate(session, sc, sc.property, rules)
    return len(scenarios)


def staleness(sc: FinancialDetails, prop: Property, rules: Rules) -> list[str]:
    """Reasons a saved result no longer matches today's inputs, rules or asking price (empty = current)."""
    calc = sc.calculated
    if not calc:
        return ["Not calculated yet."]
    reasons = []
    old_rules = calc.get("rules") or {}
    if old_rules.get("hash") != rules.rules_hash:
        reasons.append(f"The rules file changed (calculated with version {old_rules.get('version', '?')}, now {rules.version}).")
    if fs.inputs_hash(resolve_inputs(sc, prop, rules)) != calc.get("inputs_hash"):
        reasons.append("The scenario inputs, the first-time buyer setting or the Toronto detection changed since this was calculated.")
    old, now = calc.get("asking_price_at_calc"), prop.asking_price
    if (old is None) != (now is None) or (old is not None and Decimal(old) != now):
        reasons.append(f"The asking price changed from {_show(old)} to {_show(now)} since this was calculated.")
    return reasons


def _show(value: Any) -> str:
    return "not set" if value is None else f"${Decimal(str(value)):,.0f}"


# --------------------------------------------------------------------------- comparison across properties
COMPARISON_COLUMNS = [
    "Property", "Scenario", "Purchase price", "Down payment", "Total mortgage", "Monthly payment",
    "Total monthly outgoing", "Cash required", "Status",
]


def comparison_rows(properties: list[Property], rules: Rules) -> list[dict[str, Any]]:
    """One row per property using its primary scenario. Blank where there is nothing calculated."""
    rows = []
    for prop in properties:
        label = prop.address + (f", unit {prop.unit}" if prop.unit else "") + f" - {prop.city}"
        sc = primary_of(prop)
        row: dict[str, Any] = {"_property_id": str(prop.id), "Property": label, "Scenario": None,
                               "Purchase price": None, "Down payment": None, "Total mortgage": None,
                               "Monthly payment": None, "Total monthly outgoing": None, "Cash required": None}
        if sc is None:
            row["Status"] = "No primary scenario"
        else:
            row["Scenario"] = sc.scenario_name
            calc = sc.calculated
            amount = fs.result_amount
            row.update({
                "Purchase price": _f(amount(calc, "mortgage", "purchase_price")),
                "Down payment": _f(amount(calc, "mortgage", "down_payment")),
                "Total mortgage": _f(amount(calc, "mortgage", "total_mortgage")),
                "Monthly payment": _f(amount(calc, "monthly", "mortgage_payment")),
                "Total monthly outgoing": _f(amount(calc, "monthly", "total")),
                "Cash required": _f(amount(calc, "cash", "total_cash_required")),
            })
            stale = staleness(sc, prop, rules)
            if not calc:
                row["Status"] = "Not calculated"
            elif calc.get("status") != "ok":
                row["Status"] = "Blocked" if calc.get("status") == "blocked" else "Incomplete"
            else:
                row["Status"] = "Out of date" if stale else "Current"
        rows.append(row)
    return rows


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)