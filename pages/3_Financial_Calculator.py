"""Financial Calculator: mortgage, monthly cost and cash-to-close for a saved property.

Pick a property, then a scenario (a named set of assumptions). The numbers update as you type;
nothing is saved until you press Save. All rules and rates come from config/financial_rules.toml.
The maths is in services/financial_service.py; scenario handling is in services/scenarios.py.
"""
from datetime import datetime
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from auth.gate import require_login
from database import repository as repo
from database.connection import get_engine
from database.models import FinancialDetails
from services import export_service as exp
from services import scenarios as sv
from services.financial_rules import RulesError, load_rules
from services.financial_service import is_toronto_city

st.set_page_config(page_title="Financial Calculator", page_icon="🧮", layout="wide")
require_login()

S = st.session_state
S.setdefault("fc_nonce", 0)  # bump to reload the inputs from the saved scenario

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TORONTO_CHOICES = {"auto": None, "yes": True, "no": False}
MONTHLY_COSTS = [
    ("annual_property_tax", "Annual property tax", "Divided by 12 for the monthly figure."),
    ("monthly_maintenance_fee", "Maintenance fee (monthly)", None),
    ("electricity", "Electricity (monthly)", None), ("natural_gas", "Natural gas (monthly)", None),
    ("water", "Water (monthly)", None), ("home_insurance", "Home insurance (monthly)", None),
    ("internet", "Internet (monthly)", None), ("water_heater_rental", "Water heater rental (monthly)", None),
    ("repair_reserve", "Repair reserve (monthly)", "Dollars per month you set aside for repairs."),
]
ONE_TIME_COSTS = [
    ("special_assessment", "Special assessment (one-time)", "Counted in cash required, not in the monthly figure."),
    ("legal_fees", "Legal fees", "Includes disbursements. Your own lawyer's quote beats the default."),
    ("title_insurance", "Title insurance", None), ("inspection_costs", "Home inspection", None),
    ("other_closing_costs", "Other closing costs", "Appraisal, adjustments, moving, status certificate, ..."),
]


# ============================================================================ small helpers
def usd(value) -> str:
    return "-" if value is None else f"${Decimal(str(value)):,.2f}"


def pct(value, places: int = 2) -> str:
    return f"{Decimal(str(value)):.{places}f}%"


def prop_label(p) -> str:
    return p.address + (f", unit {p.unit}" if p.unit else "") + f", {p.city}"


def as_float(value):
    return None if value is None else float(value)


def dec(value, places: int = 2):
    return None if value is None else Decimal(str(round(float(value), places)))


def as_int(value):
    return None if value is None else int(value)


# ============================================================================ down payment: $ and % stay in step
def _sync(p: str) -> None:
    price = S.get(p + "price")
    if S.get(p + "basis") == "amount":
        amount = S.get(p + "dp")
        S[p + "dppct"] = round(amount / price * 100, 2) if (amount is not None and price) else None
    else:
        percent = S.get(p + "dppct")
        S[p + "dp"] = round(price * percent / 100, 2) if (percent is not None and price) else None


def _price_changed(p: str) -> None:
    _sync(p)


def _amount_changed(p: str) -> None:
    S[p + "basis"] = "amount"
    _sync(p)


def _percent_changed(p: str) -> None:
    S[p + "basis"] = "percent"
    _sync(p)


# ============================================================================ inputs
def money_input(label: str, field: str, p: str, sc, help_text: str | None = None) -> None:
    key = p + field
    S.setdefault(key, as_float(getattr(sc, field)))
    st.number_input(label, min_value=0.0, value=None, step=10.0, format="%.2f", key=key, help=help_text)


def render_inputs(sc, prop, rules, p: str) -> None:
    S.setdefault(p + "price", as_float(sc.expected_purchase_price))
    S.setdefault(p + "dp", as_float(sc.down_payment))
    S.setdefault(p + "dppct", as_float(sc.down_payment_pct))
    S.setdefault(p + "basis", sc.down_payment_basis or "percent")
    S.setdefault(p + "type", sc.mortgage_type)
    S.setdefault(p + "rate", as_float(sc.mortgage_rate))
    S.setdefault(p + "amort", sc.amortization_years)
    S.setdefault(p + "term", sc.term_years)
    S.setdefault(p + "comp", sc.compounding or "")
    S.setdefault(p + "ftb", rules.personal.first_time_buyer if sc.first_time_buyer is None else sc.first_time_buyer)
    S.setdefault(p + "nt", bool(sc.non_traditional_down_payment))
    S.setdefault(p + "tor", "auto" if sc.toronto_override is None else ("yes" if sc.toronto_override else "no"))

    st.markdown("**Purchase and mortgage**")
    a, b, c = st.columns(3)
    a.number_input("Purchase price", min_value=0.0, value=None, step=5000.0, format="%.2f", key=p + "price",
                   on_change=_price_changed, args=(p,), help="Starts as the asking price; change it to model an offer.")
    b.number_input("Down payment ($)", min_value=0.0, value=None, step=1000.0, format="%.2f", key=p + "dp",
                   on_change=_amount_changed, args=(p,), help="Edit the dollars or the percent; the last one you edit wins.")
    c.number_input("Down payment (%)", min_value=0.0, max_value=100.0, value=None, step=0.5, format="%.2f",
                   key=p + "dppct", on_change=_percent_changed, args=(p,))
    d, e, f, g = st.columns(4)
    d.selectbox("Mortgage type", ["Fixed", "Variable"], index=None, placeholder="Choose", key=p + "type")
    e.number_input("Interest rate (% per year)", min_value=0.0, max_value=100.0, value=None, step=0.05,
                   format="%.3f", key=p + "rate")
    f.number_input("Amortization (years)", min_value=1, max_value=40, value=None, step=1, key=p + "amort")
    g.number_input("Term (years, optional)", min_value=1, max_value=40, value=None, step=1, key=p + "term",
                   help="Shows the balance owing and interest paid at the end of the term.")
    h, i, j, k = st.columns(4)
    comp_default = {"Fixed": "semi-annual", "Variable": "monthly"}.get(S.get(p + "type") or "", "semi-annual for Fixed, monthly for Variable")
    h.selectbox("Compounding", ["", "semi_annual", "monthly"], key=p + "comp",
                format_func=lambda v: {"": f"Default ({comp_default})", "semi_annual": "Semi-annual", "monthly": "Monthly"}[v])
    i.checkbox("First-time buyer", key=p + "ftb",
               help="Unlocks the Ontario and Toronto rebates and 30-year insured amortization. Starts from your setting in the rules file.")
    j.checkbox("Non-traditional down payment", key=p + "nt",
               help="E.g. a borrowed down payment. Uses the 4.50% premium tier when loan-to-value is above 90%.")
    detected = "Toronto" if is_toronto_city(prop.city) else "not Toronto"
    k.selectbox("In the City of Toronto?", list(TORONTO_CHOICES), key=p + "tor",
                format_func=lambda v: {"auto": f"Auto (city says {detected})", "yes": "Yes", "no": "No"}[v])

    st.markdown("**Monthly costs**")
    for row in _chunks(MONTHLY_COSTS, 3):
        for col, (field, label, help_text) in zip(st.columns(3), row):
            with col:
                money_input(label, field, p, sc, help_text)
    st.markdown("**One-time costs at closing**")
    for row in _chunks(ONE_TIME_COSTS, 3):
        for col, (field, label, help_text) in zip(st.columns(3), row):
            with col:
                money_input(label, field, p, sc, help_text)


def _chunks(items: list, n: int):
    return [items[i:i + n] for i in range(0, len(items), n)]


def collect(p: str) -> dict:
    """Current widget values as scenario column values."""
    values = {
        "expected_purchase_price": dec(S[p + "price"]), "down_payment": dec(S[p + "dp"]),
        "down_payment_pct": dec(S[p + "dppct"]), "down_payment_basis": S[p + "basis"],
        "mortgage_rate": dec(S[p + "rate"], 3), "mortgage_type": S[p + "type"],
        "amortization_years": as_int(S[p + "amort"]), "term_years": as_int(S[p + "term"]),
        "compounding": S[p + "comp"] or None, "first_time_buyer": bool(S[p + "ftb"]),
        "toronto_override": TORONTO_CHOICES[S[p + "tor"]], "non_traditional_down_payment": bool(S[p + "nt"]),
    }
    for field, _label, _help in MONTHLY_COSTS + ONE_TIME_COSTS:
        values[field] = dec(S[p + field])
    return values


# ============================================================================ results
def table(rows: list[tuple[str, str]], key: str | None = None) -> None:
    st.dataframe(pd.DataFrame(rows, columns=["Item", "Amount"]), hide_index=True, key=key)


def render_result(calc: dict) -> None:
    status = calc["status"]
    if status == "incomplete":
        st.info(calc["errors"][0])
        return
    if status == "blocked":
        for message in calc["errors"]:
            st.error(message)
        return
    m, mo, c, cash = calc["mortgage"], calc["monthly"], calc["closing"], calc["cash"]
    for w in calc["warnings"]:
        st.warning(w)
    x, y, z, w_ = st.columns(4)
    x.metric("Total monthly outgoing", usd(mo["total"]))
    y.metric("Monthly mortgage payment", usd(m["monthly_payment"]))
    z.metric("Total cash required", usd(cash["total_cash_required"]))
    w_.metric("Total mortgage (with premium)", usd(m["total_mortgage"]))
    if calc["not_entered"]:
        st.caption("Counted as $0 because not entered: " + ", ".join(calc["not_entered"]) + ".")

    left, middle, right = st.columns(3)
    with left:
        st.markdown("**Mortgage**")
        rows = [
            ("Purchase price", usd(m["purchase_price"])),
            (f"Down payment ({pct(m['down_payment_pct'])})", usd(m["down_payment"])),
            (f"Mortgage amount (loan-to-value {pct(m['ltv_pct'])})", usd(m["base_mortgage"])),
            (f"Default insurance premium ({pct(m['premium_rate_pct'])}), added to the mortgage", usd(m["premium"])),
            ("Total mortgage", usd(m["total_mortgage"])),
            (f"{m['mortgage_type']} at {pct(m['rate_pct'], 3)}, {'semi-annual' if m['compounding'] == 'semi_annual' else 'monthly'} compounding, {m['amortization_years']} years", ""),
            ("Monthly payment", usd(m["monthly_payment"])),
            ("Total interest over the amortization", usd(m["total_interest_amortization"])),
        ]
        if "balance_at_term_end" in m:
            rows += [(f"Balance owing after {m['term_years']}-year term", usd(m["balance_at_term_end"])),
                     (f"Interest paid during the {m['term_years']}-year term", usd(m["interest_paid_in_term"])),
                     (f"Principal paid during the {m['term_years']}-year term", usd(m["principal_paid_in_term"]))]
        table(rows)
        st.caption(
            f"Stress test: qualifying rate {pct(m['stress_qualifying_rate_pct'])}, qualifying payment "
            f"{usd(m['stress_qualifying_payment'])} ({usd(m['stress_payment_increase'])} more than your payment). Informational only."
        )
    with middle:
        st.markdown("**Monthly outgoing**")
        labels = {"mortgage_payment": "Mortgage payment", "property_tax": "Property tax (annual / 12)",
                  "maintenance_fee": "Maintenance fee", "electricity": "Electricity", "natural_gas": "Natural gas",
                  "water": "Water", "home_insurance": "Home insurance", "internet": "Internet",
                  "water_heater_rental": "Water heater rental", "repair_reserve": "Repair reserve"}
        table([(label, usd(mo[k])) for k, label in labels.items()] + [("Total monthly outgoing", usd(mo["total"]))])
    with right:
        st.markdown("**Closing costs and cash required**")
        rows = [("Ontario land transfer tax", usd(c["ontario_ltt_gross"]))]
        if Decimal(c["ontario_ftb_rebate"]):
            rows.append(("First-time buyer rebate (Ontario)", "-" + usd(c["ontario_ftb_rebate"])))
        if c["toronto_applies"]:
            rows.append(("Toronto municipal land transfer tax", usd(c["toronto_mltt_gross"])))
            if Decimal(c["toronto_ftb_rebate"]):
                rows.append(("First-time buyer rebate (Toronto)", "-" + usd(c["toronto_ftb_rebate"])))
            rows.append(("Toronto administration fee (with HST)", usd(c["toronto_admin_fee_with_hst"])))
        rows += [("PST on the insurance premium", usd(c["pst_on_premium"])), ("Legal fees", usd(c["legal_fees"])),
                 ("Title insurance", usd(c["title_insurance"])), ("Inspection", usd(c["inspection_costs"])),
                 ("Other closing costs", usd(c["other_closing_costs"])),
                 ("Total closing costs", usd(c["total_closing_costs"])),
                 ("Down payment", usd(cash["down_payment"])), ("Special assessment", usd(cash["special_assessment"])),
                 ("Total cash required", usd(cash["total_cash_required"]))]
        table(rows)

    with st.expander("Show the working"):
        st.markdown(
            f"- **Insurance premium:** {m['premium_tier']}: {pct(m['premium_base_rate_pct'])}"
            + (f" + {pct(m['premium_surcharge_pct'])} for amortization over 25 years" if Decimal(m["premium_surcharge_pct"]) else "")
            + f" = {pct(m['premium_rate_pct'])} of {usd(m['base_mortgage'])} = {usd(m['premium'])}.\n"
            f"- **Monthly rate:** {m['effective_monthly_rate']} (from {pct(m['rate_pct'], 3)} per year, "
            f"{'compounded semi-annually' if m['compounding'] == 'semi_annual' else 'compounded monthly'}).\n"
            f"- **Minimum down payment for this price:** {usd(m['minimum_down_payment'])}.\n"
            f"- **Rules used:** version {calc['rules']['version']} (fingerprint {calc['rules']['hash']})."
        )
        for title, rows in (("Ontario land transfer tax", c["ontario_brackets"]), ("Toronto municipal land transfer tax", c["toronto_brackets"])):
            if rows:
                st.markdown(f"**{title}**")
                st.dataframe(pd.DataFrame([{
                    "From": usd(r["from"]), "Up to": "and above" if r["up_to"] is None else usd(r["up_to"]),
                    "Rate": pct(r["rate_pct"]), "Amount taxed": usd(r["taxed"]), "Tax": usd(r["tax"])} for r in rows]),
                    hide_index=True)


# ============================================================================ scenario management
def manage_scenarios(session: Session, prop, rules, scenarios, current) -> None:
    with st.expander("Manage scenarios"):
        st.caption("A scenario is a named set of assumptions for this property. The primary scenario (⭐) is the one "
                   "Property Master and the property comparison use.")
        n1, n2 = st.columns([3, 1])
        new_name = n1.text_input("New scenario name", key=f"fc_new_{prop.id}", placeholder="e.g. 10% down, 30-year")
        if n2.button("Create", key=f"fc_create_{prop.id}"):
            try:
                created = sv.create_scenario(session, prop, rules, new_name)
            except ValueError as exc:
                st.error(str(exc))
            else:
                session.commit()
                S.fc_select_next = str(created.id)
                S.fc_flash = f"Created scenario {created.scenario_name!r}, pre-filled from the listing."
                st.rerun()
        if current is None:
            return
        d1, d2 = st.columns([3, 1])
        dup_name = d1.text_input("Copy this scenario as", key=f"fc_dup_{current.id}", placeholder="New name")
        if d2.button("Duplicate", key=f"fc_dupbtn_{current.id}"):
            try:
                copy = sv.duplicate_scenario(session, current, dup_name, rules)
            except ValueError as exc:
                st.error(str(exc))
            else:
                session.commit()
                S.fc_select_next = str(copy.id)
                S.fc_flash = f"Copied to {copy.scenario_name!r}."
                st.rerun()
        r1, r2 = st.columns([3, 1])
        rename = r1.text_input("Rename this scenario", value=current.scenario_name, key=f"fc_ren_{current.id}")
        if r2.button("Rename", key=f"fc_renbtn_{current.id}"):
            try:
                sv.rename_scenario(session, current, rename)
            except ValueError as exc:
                st.error(str(exc))
            else:
                session.commit()
                S.fc_flash = f"Renamed to {current.scenario_name!r}."
                st.rerun()
        p1, p2 = st.columns([3, 1])
        p1.write("This is the primary scenario." if current.is_primary else "Make this the scenario Property Master shows.")
        if p2.button("Set as primary", key=f"fc_prim_{current.id}", disabled=current.is_primary):
            sv.set_primary(session, current)
            session.commit()
            S.fc_flash = f"{current.scenario_name!r} is now the primary scenario."
            st.rerun()
        sure = st.checkbox(f"Delete {current.scenario_name!r} permanently", key=f"fc_delok_{current.id}")
        if st.button("Delete scenario", key=f"fc_delbtn_{current.id}", disabled=not sure):
            name = current.scenario_name
            was_primary = sv.delete_scenario(session, current)
            session.commit()
            S.fc_flash = f"Deleted {name!r}." + (" This property now has no primary scenario." if was_primary else "")
            st.rerun()


def render_calculator(session: Session, prop, rules) -> None:
    scenarios = repo.scenarios_for_property(session, prop.id)
    if not scenarios:
        st.info("This property has no financial scenario yet.")
        manage_scenarios(session, prop, rules, scenarios, None)
        name = st.text_input("Name for the first scenario", value="Default", key=f"fc_first_{prop.id}")
        if st.button("Create the first scenario", type="primary", key=f"fc_firstbtn_{prop.id}"):
            try:
                sv.create_scenario(session, prop, rules, name)
            except ValueError as exc:
                st.error(str(exc))
            else:
                session.commit()
                S.fc_flash = "Scenario created, pre-filled from the listing. Enter the mortgage details."
                st.rerun()
        return

    ids = [str(s.id) for s in scenarios]
    sel_key = f"fc_scenario_{prop.id}"
    nxt = S.pop("fc_select_next", None)
    if nxt in ids:
        S[sel_key] = nxt
    if S.get(sel_key) not in ids:
        S[sel_key] = ids[0]
    names = {str(s.id): ("⭐ " if s.is_primary else "") + s.scenario_name for s in scenarios}
    chosen = st.selectbox("Scenario", ids, key=sel_key, format_func=names.get)
    current = next(s for s in scenarios if str(s.id) == chosen)
    manage_scenarios(session, prop, rules, scenarios, current)

    reasons = sv.staleness(current, prop, rules)
    if reasons and current.calculated:
        st.warning("This saved result is out of date:\n\n" + "\n".join(f"- {r}" for r in reasons))
        if st.button("Recalculate this scenario", key=f"fc_recalc_{current.id}"):
            sv.recalculate(session, current, prop, rules)
            session.commit()
            S.fc_flash = "Recalculated and saved."
            st.rerun()
    elif current.calculated_at:
        st.caption(f"Saved result calculated {current.calculated_at:%Y-%m-%d %H:%M} UTC with rules version "
                   f"{(current.calculated.get('rules') or {}).get('version', '?')}.")

    p = f"fc_{current.id}_{S.fc_nonce}_"
    render_inputs(current, prop, rules, p)

    tmp = FinancialDetails()
    sv.apply_inputs(tmp, collect(p))
    live = sv.calculate_only(tmp, prop, rules)
    unsaved = sv.resolve_inputs(tmp, prop, rules) != sv.resolve_inputs(current, prop, rules)

    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("Save scenario", type="primary", key=f"fc_save_{current.id}"):
        sv.apply_inputs(current, collect(p))
        sv.recalculate(session, current, prop, rules)
        session.commit()
        S.fc_flash = f"Saved {current.scenario_name!r} and its calculated results."
        st.rerun()
    if b2.button("Discard changes", key=f"fc_reset_{current.id}", disabled=not unsaved):
        S.fc_nonce += 1
        st.rerun()
    st.markdown("### Results" + ("  (preview: you have unsaved changes)" if unsaved else ""))
    render_result(live)


# ============================================================================ comparisons and rules
def render_scenario_comparison(session: Session, prop, rules) -> None:
    scenarios = repo.scenarios_for_property(session, prop.id)
    if not scenarios:
        st.info("No scenarios for this property yet.")
        return
    lines = [("Status", None), ("Purchase price", ("mortgage", "purchase_price")), ("Down payment", ("mortgage", "down_payment")),
             ("Total mortgage", ("mortgage", "total_mortgage")), ("Monthly payment", ("mortgage", "monthly_payment")),
             ("Property tax / month", ("monthly", "property_tax")), ("Total monthly outgoing", ("monthly", "total")),
             ("Closing costs", ("cash", "closing_costs")), ("Total cash required", ("cash", "total_cash_required")),
             ("Stress-test payment", ("mortgage", "stress_qualifying_payment"))]
    data: dict[str, list[str]] = {}
    for s in scenarios:
        calc, col = s.calculated, []
        stale = bool(sv.staleness(s, prop, rules)) and bool(calc)
        for label, path in lines:
            if path is None:
                col.append("Not calculated" if not calc else {"ok": "Out of date" if stale else "Current", "blocked": "Blocked", "incomplete": "Incomplete"}[calc["status"]])
            else:
                value = (calc or {}).get(path[0], {}).get(path[1]) if calc and calc["status"] == "ok" else None
                col.append(usd(value) if value is not None else "-")
        data[("⭐ " if s.is_primary else "") + s.scenario_name] = col
    st.dataframe(pd.DataFrame(data, index=[l for l, _ in lines]))
    st.caption("Saved results only. Save a scenario on the Calculator tab to update its column.")


def render_property_comparison(session: Session, rules) -> None:
    props = repo.list_properties(session)
    rows = sv.comparison_rows(props, rules)
    df = pd.DataFrame(rows)
    current = sum(1 for r in rows if r["Status"] == "Current")
    stale = sum(1 for r in rows if r["Status"] == "Out of date")
    st.caption(f"{len(rows)} properties, {current} with a current result, {stale} out of date. "
               "Each property uses its primary scenario. Click a header to sort.")
    money_cols = ["Purchase price", "Down payment", "Total mortgage", "Monthly payment", "Total monthly outgoing", "Cash required"]
    cfg = {c: st.column_config.NumberColumn(c, format="$%.2f") for c in money_cols}
    st.dataframe(df[sv.COMPARISON_COLUMNS], hide_index=True, column_config=cfg)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    kinds = ["text", "text"] + ["money"] * 6 + ["text"]
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download Excel (.xlsx)", exp.build_table_xlsx(df, sv.COMPARISON_COLUMNS, kinds, "Property comparison"),
                       file_name=f"property-comparison-{stamp}.xlsx", mime=XLSX_MIME)
    c2.download_button("Download CSV", exp.build_table_csv(df, sv.COMPARISON_COLUMNS),
                       file_name=f"property-comparison-{stamp}.csv", mime="text/csv")
    if c3.button("Recalculate all scenarios"):
        count = sv.recalculate_all(session, rules)
        session.commit()
        S.fc_flash = f"Recalculated {count} scenario(s) with the current rules."
        st.rerun()


def render_rules(rules) -> None:
    st.markdown(f"**Rules version {rules.version}** (retrieved {rules.retrieved}). Edit `config/financial_rules.toml` and save "
                "to change any rate, bracket, rebate or limit; saved scenarios are then flagged out of date until recalculated.")
    st.markdown(f"**Your first-time buyer setting:** {'yes' if rules.personal.first_time_buyer else 'no'}. "
                "Change it in the `[personal]` section of the rules file; each scenario can still override it.")
    a, b = st.columns(2)
    a.markdown("**Default insurance premium**")
    a.dataframe(pd.DataFrame([{"Loan-to-value up to": pct(t.max_ltv_pct, 0), "Premium": pct(t.rate_pct)} for t in rules.premium_tiers]), hide_index=True)
    a.caption(f"Plus {pct(rules.extended_amortization_surcharge_pct)} when amortization is over {rules.extended_amortization_over_years} years; "
              f"{pct(rules.non_traditional_rate_pct)} with a non-traditional down payment above {pct(rules.non_traditional_min_ltv_pct, 0)} loan-to-value; "
              f"PST on the premium {pct(rules.pst_on_premium_pct, 0)}.")
    b.markdown("**Ontario land transfer tax**")
    b.dataframe(pd.DataFrame([{"Up to": "and above" if x.up_to is None else usd(x.up_to), "Rate": pct(x.rate_pct)} for x in rules.ontario_brackets]), hide_index=True)
    st.markdown("**Toronto municipal land transfer tax**")
    st.dataframe(pd.DataFrame([{"Up to": "and above" if x.up_to is None else usd(x.up_to), "Rate": pct(x.rate_pct)} for x in rules.toronto_brackets]), hide_index=True)
    st.markdown("**Sources and how well each was confirmed**")
    for s in rules.sources:
        link = f" ([link]({s['url']}))" if s.get("url") else ""
        st.markdown(f"- {s.get('label', s.get('id'))}{link}  \n  *Confidence: {s.get('confidence', 'not stated')}*")


# ============================================================================ page
st.title("Financial Calculator")
if S.get("fc_flash"):
    st.success(S.pop("fc_flash"))

try:
    rules = load_rules()
except RulesError as exc:
    st.error(f"The rules file could not be used: {exc}")
    st.stop()

try:
    session = Session(get_engine())
    properties = repo.list_properties(session)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not read the database: {type(exc).__name__}. Check the connection and try again.")
    st.stop()

with session:
    if not properties:
        st.info("No properties saved yet. Use Add Property in the sidebar first.")
        st.stop()
    by_id = {str(p.id): p for p in properties}
    order = sorted(by_id, key=lambda i: prop_label(by_id[i]).lower())
    if S.get("fc_property") not in by_id:
        S.pop("fc_property", None)
    picked = st.selectbox("Property", order, key="fc_property", format_func=lambda i: prop_label(by_id[i]))
    prop = by_id[picked]
    ask = f"asking {usd(prop.asking_price)}" if prop.asking_price is not None else "no asking price saved"
    st.caption(f"{prop.property_type or 'Property'} in {prop.city}, {ask}.")

    tab_calc, tab_scen, tab_props, tab_rules = st.tabs(
        ["Calculator", "Compare scenarios", "Compare properties", "Rules and sources"])
    with tab_calc:
        render_calculator(session, prop, rules)
    with tab_scen:
        render_scenario_comparison(session, prop, rules)
    with tab_props:
        render_property_comparison(session, rules)
    with tab_rules:
        render_rules(rules)