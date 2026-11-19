"""Canadian mortgage and closing-cost maths for the Financial Calculator.

Pure functions on exact Decimals: no database, no Streamlit. Every rate, bracket, rebate and
limit comes from the Rules object (config/financial_rules.toml); none is written here.

Conventions
- Money is rounded to cents with ROUND_HALF_UP at each reported line.
- Interest rate is the annual nominal rate. The monthly rate is (1 + j/m)^(m/12) - 1, where m is
  2 for semi-annual and 12 for monthly compounding.
- Balances and interest are produced by stepping through the monthly schedule the way a lender
  does (interest rounded to the cent each month, final payment adjusts). The payment itself is the
  standard annuity formula rounded to the cent.
- Only insured (high-ratio) mortgages are modelled. A scenario outside the rules is BLOCKED with a
  clear message instead of being calculated, because the numbers would not be real.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from services.financial_rules import Bracket, Rules

ZERO = Decimal(0)
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
COMPOUNDING_PER_YEAR = {"semi_annual": 2, "monthly": 12}

REQUIRED_INPUTS = [
    ("price", "purchase price"), ("down_payment", "down payment"), ("rate_pct", "mortgage rate"),
    ("mortgage_type", "mortgage type"), ("amortization_years", "amortization"),
]
OPTIONAL_COSTS = [
    ("annual_property_tax", "Annual property tax"), ("maintenance_fee", "Monthly maintenance fee"),
    ("electricity", "Electricity"), ("natural_gas", "Natural gas"), ("water", "Water"),
    ("home_insurance", "Home insurance"), ("internet", "Internet"),
    ("water_heater_rental", "Water heater rental"), ("repair_reserve", "Repair reserve"),
    ("special_assessment", "Special assessment"), ("legal_fees", "Legal fees"),
    ("title_insurance", "Title insurance"), ("inspection_costs", "Inspection"),
    ("other_closing_costs", "Other closing costs"),
]


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class ScenarioInputs:
    """Resolved inputs for one calculation. None means "not entered"."""
    price: Decimal | None = None
    down_payment: Decimal | None = None
    rate_pct: Decimal | None = None
    mortgage_type: str | None = None
    amortization_years: int | None = None
    term_years: int | None = None
    compounding: str | None = None              # None = default for the mortgage type
    first_time_buyer: bool = False
    non_traditional: bool = False
    in_toronto: bool = False
    annual_property_tax: Decimal | None = None
    maintenance_fee: Decimal | None = None
    electricity: Decimal | None = None
    natural_gas: Decimal | None = None
    water: Decimal | None = None
    home_insurance: Decimal | None = None
    internet: Decimal | None = None
    water_heater_rental: Decimal | None = None
    repair_reserve: Decimal | None = None
    special_assessment: Decimal | None = None
    legal_fees: Decimal | None = None
    title_insurance: Decimal | None = None
    inspection_costs: Decimal | None = None
    other_closing_costs: Decimal | None = None


# --------------------------------------------------------------------------- mortgage maths
def periodic_rate(annual_pct: Decimal, compounding: str) -> Decimal:
    """Equivalent monthly rate for an annual nominal rate with the given compounding."""
    m = COMPOUNDING_PER_YEAR[compounding]
    with localcontext() as ctx:
        ctx.prec = 50
        return (1 + (annual_pct / HUNDRED) / m) ** (Decimal(m) / 12) - 1


def monthly_payment(principal: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    """Level monthly payment that repays `principal` over `months` (standard annuity), to the cent."""
    with localcontext() as ctx:
        ctx.prec = 50
        if monthly_rate == 0:
            return money(principal / months)
        return money(principal * monthly_rate / (1 - (1 + monthly_rate) ** (-months)))


def run_schedule(principal: Decimal, monthly_rate: Decimal, payment: Decimal, months: int,
                 checkpoint: int | None = None) -> dict[str, Decimal]:
    """Step through the schedule month by month. Returns the balance and interest paid after
    `checkpoint` payments (if given) and the totals over the whole amortization."""
    balance, interest_total, payments_total = principal, ZERO, ZERO
    at_balance = at_interest = None
    for k in range(1, months + 1):
        interest = money(balance * monthly_rate)
        due = balance + interest
        pay = due if (k == months or due <= payment) else payment
        balance = money(due - pay)
        interest_total += interest
        payments_total += pay
        if checkpoint is not None and k == checkpoint:
            at_balance, at_interest = balance, interest_total
        if balance <= 0:
            break
    out = {"total_interest": interest_total, "total_paid": payments_total}
    if checkpoint is not None:
        out["balance_at_checkpoint"] = at_balance if at_balance is not None else ZERO
        out["interest_at_checkpoint"] = at_interest if at_interest is not None else interest_total
    return out


# --------------------------------------------------------------------------- insurance rules
def minimum_down_payment(price: Decimal, bands: tuple[Bracket, ...]) -> Decimal:
    """Marginal minimum: pct of the part of the price inside each band (e.g. 5% to $500K, 10% beyond)."""
    total, lower = ZERO, ZERO
    for band in bands:
        if price <= lower:
            break
        upper = band.up_to if band.up_to is not None else price
        total += (min(price, upper) - lower) * band.rate_pct / HUNDRED
        lower = upper
    return money(total)


def premium_for(loan: Decimal, price: Decimal, amortization_years: int, non_traditional: bool,
                rules: Rules) -> dict[str, Any]:
    """Default-insurance premium. Loan-to-value comparisons are exact (loan*100 <= max_ltv*price)."""
    base, label, nt_applied = None, "", False
    if non_traditional and loan * HUNDRED > rules.non_traditional_min_ltv_pct * price:
        base, nt_applied = rules.non_traditional_rate_pct, True
        label = f"Non-traditional down payment, above {rules.non_traditional_min_ltv_pct:f}% loan-to-value"
    else:
        previous = ZERO
        for tier in rules.premium_tiers:
            if loan * HUNDRED <= tier.max_ltv_pct * price:
                base = tier.rate_pct
                label = (f"Up to {tier.max_ltv_pct:f}% loan-to-value" if previous == 0
                         else f"Over {previous:f}% up to {tier.max_ltv_pct:f}% loan-to-value")
                break
            previous = tier.max_ltv_pct
    surcharge = (rules.extended_amortization_surcharge_pct
                 if amortization_years > rules.extended_amortization_over_years else ZERO)
    return {"base_rate_pct": base, "surcharge_pct": surcharge, "tier": label, "non_traditional_applied": nt_applied,
            "rate_pct": None if base is None else base + surcharge,
            "premium": None if base is None else money(loan * (base + surcharge) / HUNDRED)}


# --------------------------------------------------------------------------- land transfer tax
def land_transfer_tax(price: Decimal, brackets: tuple[Bracket, ...]) -> tuple[Decimal, list[dict[str, Decimal | None]]]:
    """Marginal tax over the brackets. Returns (total to the cent, per-bracket working)."""
    rows, lower, total = [], ZERO, ZERO
    for b in brackets:
        upper = b.up_to
        taxed = max(ZERO, (min(price, upper) if upper is not None else price) - lower)
        tax = taxed * b.rate_pct / HUNDRED
        rows.append({"from": lower, "up_to": upper, "rate_pct": b.rate_pct, "taxed": money(taxed), "tax": money(tax)})
        total += tax
        if upper is None or price <= upper:
            break
        lower = upper
    return money(total), rows


def is_toronto_city(city: str | None) -> bool:
    return (city or "").strip().casefold() == "toronto"


# --------------------------------------------------------------------------- inputs
def _jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _canonical(value: Any) -> Any:
    """Numbers written differently but equal (500000, 500000.00, 5E+5) must give the same fingerprint."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def inputs_json(inp: ScenarioInputs) -> dict[str, Any]:
    return {f.name: _canonical(getattr(inp, f.name)) for f in fields(inp)}


def inputs_hash(inp: ScenarioInputs) -> str:
    return hashlib.sha256(json.dumps(inputs_json(inp), sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- the calculation
def _pct2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return f"${value:,.0f}" if value == value.to_integral_value() else f"${value:,.2f}"


def calculate(inp: ScenarioInputs, rules: Rules) -> dict[str, Any]:
    """Run the full calculation. Returns a JSON-friendly dict.

    status "incomplete": a required input is missing. status "blocked": a rule is broken (errors says
    which). status "ok": numbers are in `mortgage`, `monthly`, `closing` and `cash`.
    """
    result: dict[str, Any] = {
        "status": "ok", "errors": [], "warnings": [], "not_entered": [],
        "rules": {"version": rules.version, "hash": rules.rules_hash},
        "inputs": inputs_json(inp), "inputs_hash": inputs_hash(inp),
    }
    missing = [label for attr, label in REQUIRED_INPUTS if getattr(inp, attr) in (None, "")]
    if missing:
        result["status"] = "incomplete"
        result["errors"] = ["Enter " + ", ".join(missing) + " to calculate."]
        return result

    price, down, rate = inp.price, inp.down_payment, inp.rate_pct
    amort, errors = inp.amortization_years, result["errors"]
    if inp.mortgage_type not in rules.default_compounding:
        errors.append(f"Mortgage type must be one of: {', '.join(rules.default_compounding)}.")
    if price <= 0:
        errors.append("Purchase price must be more than $0.")
    if down < 0 or (price > 0 and down >= price):
        errors.append("Down payment must be at least $0 and less than the purchase price.")
    if not (ZERO <= rate <= HUNDRED):
        errors.append("Mortgage rate must be between 0% and 100%.")
    if inp.compounding is not None and inp.compounding not in COMPOUNDING_PER_YEAR:
        errors.append("Compounding must be semi-annual or monthly.")
    if errors:
        result["status"] = "blocked"
        return result

    max_amort = rules.max_amortization_years_ftb if inp.first_time_buyer else rules.max_amortization_years
    if amort < 1 or amort > max_amort:
        if inp.first_time_buyer or amort > rules.max_amortization_years_ftb:
            errors.append(f"Amortization must be between 1 and {rules.max_amortization_years_ftb} years for an insured mortgage.")
        else:
            errors.append(
                f"Insured mortgages are limited to {rules.max_amortization_years} years unless you are a first-time buyer "
                f"(then up to {rules.max_amortization_years_ftb}). Tick first-time buyer if you qualify, or shorten the amortization."
            )
    if inp.term_years is not None and not (1 <= inp.term_years <= max(amort, 1)):
        errors.append("Term must be at least 1 year and no longer than the amortization.")

    min_down = minimum_down_payment(price, rules.down_payment_minimum) if price < rules.max_insurable_price else None
    if price >= rules.max_insurable_price:
        errors.append(
            f"Insured mortgages are only available below {_fmt(rules.max_insurable_price)}; a home at {_fmt(price)} needs "
            f"{rules.uninsured_down_payment_pct:f}% down. This calculator models insured mortgages only."
        )
    elif down * HUNDRED >= price * rules.uninsured_down_payment_pct:
        errors.append(
            f"A down payment of {rules.uninsured_down_payment_pct:f}% or more does not need mortgage default insurance. "
            "This calculator models insured (high-ratio) mortgages only."
        )
    elif down < min_down:
        errors.append(
            f"The minimum down payment for a {_fmt(price)} home is {_fmt(min_down)} "
            f"({' + '.join(f'{b.rate_pct:f}% of the part up to {_fmt(b.up_to)}' if i == 0 else f'{b.rate_pct:f}% of the rest' for i, b in enumerate(rules.down_payment_minimum))}). "
            f"You entered {_fmt(down)}."
        )
    if errors:
        result["status"] = "blocked"
        return result

    loan = money(price - down)
    ins = premium_for(loan, price, amort, inp.non_traditional, rules)
    if ins["base_rate_pct"] is None:
        result["status"] = "blocked"
        errors.append(f"Loan-to-value is above {rules.max_ltv_pct:f}%, which cannot be insured.")
        return result
    if inp.non_traditional and not ins["non_traditional_applied"]:
        result["warnings"].append(
            f"The non-traditional down payment rate only applies above {rules.non_traditional_min_ltv_pct:f}% loan-to-value; the standard rate was used.")

    premium = ins["premium"]
    total_mortgage = loan + premium
    compounding = inp.compounding or rules.default_compounding[inp.mortgage_type]
    r = periodic_rate(rate, compounding)
    n = amort * 12
    payment = monthly_payment(total_mortgage, r, n)
    term_months = inp.term_years * 12 if inp.term_years else None
    sched = run_schedule(total_mortgage, r, payment, n, checkpoint=term_months)

    q_rate = max(rate + rules.stress_buffer_pct, rules.stress_floor_pct)
    q_payment = monthly_payment(total_mortgage, periodic_rate(q_rate, compounding), n)

    mortgage: dict[str, Any] = {
        "purchase_price": money(price), "down_payment": money(down),
        "down_payment_pct": (down / price * HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP),
        "minimum_down_payment": min_down,
        "base_mortgage": loan, "ltv_pct": (loan / price * HUNDRED).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        "premium_base_rate_pct": _pct2(ins["base_rate_pct"]), "premium_surcharge_pct": _pct2(ins["surcharge_pct"]),
        "premium_rate_pct": _pct2(ins["rate_pct"]), "premium_tier": ins["tier"], "premium": premium,
        "total_mortgage": total_mortgage,
        "mortgage_type": inp.mortgage_type, "compounding": compounding,
        "rate_pct": rate.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
        "effective_monthly_rate": r.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        "amortization_years": amort, "monthly_payment": payment,
        "total_interest_amortization": money(sched["total_interest"]),
        "stress_qualifying_rate_pct": _pct2(q_rate), "stress_qualifying_payment": q_payment,
        "stress_payment_increase": money(q_payment - payment),
    }
    if term_months:
        mortgage.update({
            "term_years": inp.term_years, "balance_at_term_end": sched["balance_at_checkpoint"],
            "principal_paid_in_term": money(total_mortgage - sched["balance_at_checkpoint"]),
            "interest_paid_in_term": sched["interest_at_checkpoint"],
        })

    def amount(name: str) -> Decimal:
        v = getattr(inp, name)
        return money(0) if v is None else money(v)

    monthly = {
        "mortgage_payment": payment, "property_tax": money(amount("annual_property_tax") / 12),
        "maintenance_fee": amount("maintenance_fee"), "electricity": amount("electricity"),
        "natural_gas": amount("natural_gas"), "water": amount("water"), "home_insurance": amount("home_insurance"),
        "internet": amount("internet"), "water_heater_rental": amount("water_heater_rental"),
        "repair_reserve": amount("repair_reserve"),
    }
    monthly["total"] = sum(monthly.values(), ZERO)

    on_gross, on_rows = land_transfer_tax(price, rules.ontario_brackets)
    on_rebate = money(min(on_gross, rules.ontario_rebate_max)) if inp.first_time_buyer else money(0)
    to_gross = to_rebate = to_fee = money(0)
    to_rows: list[dict] = []
    if inp.in_toronto:
        to_gross, to_rows = land_transfer_tax(price, rules.toronto_brackets)
        to_rebate = money(min(to_gross, rules.toronto_rebate_max)) if inp.first_time_buyer else money(0)
        to_fee = money(rules.toronto_admin_fee * (1 + rules.toronto_hst_pct / HUNDRED))
    pst = money(premium * rules.pst_on_premium_pct / HUNDRED)
    closing = {
        "ontario_ltt_gross": on_gross, "ontario_ftb_rebate": on_rebate, "ontario_ltt_net": on_gross - on_rebate,
        "toronto_applies": inp.in_toronto, "toronto_mltt_gross": to_gross, "toronto_ftb_rebate": to_rebate,
        "toronto_mltt_net": to_gross - to_rebate, "toronto_admin_fee_with_hst": to_fee,
        "pst_on_premium": pst, "legal_fees": amount("legal_fees"), "title_insurance": amount("title_insurance"),
        "inspection_costs": amount("inspection_costs"), "other_closing_costs": amount("other_closing_costs"),
        "ontario_brackets": on_rows, "toronto_brackets": to_rows,
    }
    closing["total_closing_costs"] = (
        closing["ontario_ltt_net"] + closing["toronto_mltt_net"] + to_fee + pst + closing["legal_fees"]
        + closing["title_insurance"] + closing["inspection_costs"] + closing["other_closing_costs"]
    )
    special = amount("special_assessment")
    cash = {"down_payment": money(down), "closing_costs": closing["total_closing_costs"],
            "special_assessment": special, "total_cash_required": money(down) + closing["total_closing_costs"] + special}

    if inp.first_time_buyer:
        result["warnings"].append("First-time buyer rebates and the longer insured amortization assume you qualify.")
    result["not_entered"] = [label for attr, label in OPTIONAL_COSTS if getattr(inp, attr) is None]
    result.update({"mortgage": mortgage, "monthly": monthly, "closing": closing, "cash": cash})
    return _jsonable(result)


# --------------------------------------------------------------------------- reading results back
def result_amount(calculated: dict | None, section: str, key: str) -> Decimal | None:
    """A money value from a saved result, or None when the result is missing or not "ok"."""
    if not calculated or calculated.get("status") != "ok":
        return None
    value = (calculated.get(section) or {}).get(key)
    return None if value is None else Decimal(value)