"""Estimate and save monthly property costs for one selected property at a time.

Costs are saved to the existing financial_details scenario columns. No new table is
needed. Existing scenario values are protected unless replacement is explicitly chosen.
"""
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import date

from auth.gate import require_login
from database import repository as repo
from database.connection import get_engine
from services import scenarios as sv
from services.financial_rules import RulesError, load_rules
from services.property_cost_estimator import (
    EstimationError, estimate_costs, lookup_postal, normalize_ontario_postal, read_config,
)
from services.oeb_live_rates import (
    GAS_ZONES,
    RateUnavailable,
    fetch_tiered_electricity,
    fetch_gas_supply,
    tiered_energy_cost,
    gas_supply_cost,
)
from utils.normalization import build_address_key

st.set_page_config(page_title="Monthly Cost Estimator", page_icon="💡", layout="wide")
require_login()
S = st.session_state


def label(prop):
    return f"{prop.address or '(address missing)'}{', unit ' + prop.unit if prop.unit else ''}, {prop.city or '(city missing)'}"


def fmt(value):
    return "—" if value is None else f"${Decimal(str(value)):,.2f}"


st.title("Monthly Cost Estimator")
st.caption("Select one property. Estimates are planning figures, not actual utility bills. "
           "Only Save writes financial scenario values to Neon PostgreSQL.")
if S.get("mce_flash"):
    st.success(S.pop("mce_flash"))

try:
    rules = load_rules()
    cfg = read_config()
except (RulesError, OSError, KeyError, ValueError) as exc:
    st.error(f"Could not load estimation configuration: {exc}")
    st.stop()

with Session(get_engine()) as session:
    props = repo.list_properties(session)
    if not props:
        st.info("No saved properties. Add a property first.")
        st.stop()

    rows = [{
        "Address": label(p), "City": p.city or "", "Postal code": p.postal_code or "Missing",
        "Property type": p.property_type or "", "Asking price": float(p.asking_price) if p.asking_price is not None else None,
        "Scenarios": len(p.financials), "ID": str(p.id),
    } for p in props]
    st.markdown("**Select a property (one row only)**")
    selected = st.dataframe(
        pd.DataFrame(rows).drop(columns=["ID"]), hide_index=True, width='stretch',
        on_select="rerun", selection_mode="single-row", key="mce_property_table",
    )
    indices = selected.selection.rows
    if not indices:
        st.info("Click one row to view its postal code and calculate monthly estimates.")
        st.stop()
    prop = props[indices[0]]
    property_key = str(prop.id)
    prefix = f"mce_{property_key}_"
    st.subheader(label(prop))

    st.markdown("**1. Verify the postal code**")
    postal = st.text_input("Ontario postal code", value=prop.postal_code or "", key=prefix + "postal",
                           help="A postal code helps check location, but cannot reveal actual utility bills.")
    if not prop.postal_code:
        st.warning("Postal code missing. Enter and save the correct code before estimating.")
    if st.button("Check approximate postal location", key=prefix + "lookup"):
        try:
            found = lookup_postal(postal)
            if found:
                st.info("Approximate lookup: " + found.get("display_name", "No description"))
                if found.get("city") and prop.city and found["city"].casefold() != prop.city.casefold():
                    st.warning("Lookup city differs from the saved city; verify the address manually.")
            else:
                st.warning("No lookup result. This does not prove the postal code is incorrect.")
        except EstimationError as exc:
            st.error(str(exc))
    if st.button("Save postal code to Property Master", key=prefix + "save_postal"):
        try:
            normalized = normalize_ontario_postal(postal)
            if not prop.address or not prop.city:
                raise EstimationError("Add the property's address and city in Property Master first.")
            prop.postal_code = normalized
            prop.address_key = build_address_key(prop.address, prop.unit, normalized)
            prop.manual_overrides = {**(prop.manual_overrides or {}), "postal_code": True}
            repo.refresh_quality(prop)
            session.commit()
            S.pop(prefix + "results", None)
            S.mce_flash = "Postal code saved to the property record."
            st.rerun()
        except (EstimationError, IntegrityError) as exc:
            session.rollback()
            st.error(f"Postal code was not saved: {exc}")

    if not prop.postal_code:
        st.stop()
    if postal.strip().upper().replace(" ", "") != prop.postal_code.replace(" ", ""):
        st.warning("Save the edited postal code before calculating estimates.")
        st.stop()

    scenarios = repo.scenarios_for_property(session, prop.id)
    st.markdown("**2. Set monthly usage assumptions**")
    a = cfg["assumptions"]
    x, y, z = st.columns(3)
    kwh = x.number_input("Electricity (kWh/month)", min_value=0.0, value=float(a["monthly_electricity_kwh"]), key=prefix + "kwh")
    gas = y.number_input("Natural gas (m³/month)", min_value=0.0, value=float(a["monthly_natural_gas_m3"]), key=prefix + "gas")
    water = z.number_input("Water (m³/month)", min_value=0.0, value=float(a["monthly_water_m3"]), key=prefix + "water")
    c1, c2 = st.columns(2)
    uses_gas = c1.checkbox("Property uses natural gas", value=True, key=prefix + "uses_gas")
    water_separate = c2.checkbox("Water is billed separately", value=True, key=prefix + "water_separate")
    c3, c4 = st.columns(2)
    insurance = c3.checkbox("Include insurance budget", value=True, key=prefix + "insurance")
    repairs = c4.checkbox("Include repair reserve", value=True, key=prefix + "repairs")

    st.markdown("**Live Ontario Energy Board rates**")
    st.caption(
        "OEB commodity rates are not complete monthly bills. "
        "Select the correct gas zone from your utility bill."
    )

    gas_zone = st.selectbox(
        "Natural gas utility / rate zone",
        ["Not selected", *GAS_ZONES],
        key=prefix + "gas_zone",
    )

    if st.button("Fetch current OEB rates", key=prefix + "fetch_oeb"):
        try:
            tier1, tier2 = fetch_tiered_electricity()
            gas_rate = None

            if uses_gas and gas_zone != "Not selected":
                gas_rate = fetch_gas_supply(gas_zone)

            S[prefix + "oeb_rates"] = {
                "electricity": (tier1, tier2),
                "gas": gas_rate,
                "gas_zone": gas_zone,
            }
            st.success("Published OEB rates retrieved.")

        except (RateUnavailable, OSError, ValueError) as exc:
            S.pop(prefix + "oeb_rates", None)
            st.error(f"Could not verify OEB rates: {exc}")

    live_rates = S.get(prefix + "oeb_rates")

    if live_rates:
        tier1, tier2 = live_rates["electricity"]

        # Tiered thresholds: 1,000 kWh in winter,
        # 600 kWh in summer for residential customers.
        winter = date.today().month in (11, 12, 1, 2, 3, 4)

        electricity_energy = tiered_energy_cost(
            Decimal(str(kwh)), tier1, tier2, winter=winter
        )

        st.write(
            f"Electricity Tiered energy charge: "
            f"${electricity_energy:,.2f}/month"
        )
        st.caption(
            f"Tier 1: {tier1.rate_cents}¢/kWh | "
            f"Tier 2: {tier2.rate_cents}¢/kWh | "
            f"Effective: {tier1.effective}"
        )
        st.link_button("OEB electricity source", tier1.source_url)

        gas_rate = live_rates["gas"]

        if gas_rate and uses_gas:
            gas_energy = gas_supply_cost(
                Decimal(str(gas)), gas_rate
            )

            st.write(
                f"Natural gas supply charge: "
                f"${gas_energy:,.2f}/month"
            )
            st.caption(
                f"{live_rates['gas_zone']} | "
                f"{gas_rate.rate_cents}¢/m³ | "
                f"Effective: {gas_rate.effective}"
            )
            st.link_button("OEB natural gas source", gas_rate.source_url)

        st.warning(
            "These are commodity charges only. They exclude "
            "delivery, fixed charges, taxes, adjustments and "
            "applicable rebates. They have not been applied "
            "to the scenario's monthly bills."
        )

    st.caption("Electricity, gas, and insurance use editable planning assumptions from "
               "config/utility_estimation_rules.toml. Toronto water uses the configured Toronto rate; "
               "no water estimate is generated for other municipalities.")

    if st.button("Calculate estimates", type="primary", key=prefix + "calculate"):
        try:
            price = prop.expected_offer_price or prop.asking_price
            estimates = estimate_costs(
                city=prop.city or "", postal_code=prop.postal_code, purchase_price=price,
                electricity_kwh=Decimal(str(kwh)), gas_m3=Decimal(str(gas)), water_m3=Decimal(str(water)),
                uses_natural_gas=uses_gas, water_billed_separately=water_separate,
                include_insurance=insurance, include_repair_reserve=repairs, config=cfg,
            )
            S[prefix + "results"] = estimates
            S[prefix + "context"] = (prop.postal_code, str(price), kwh, gas, water,
                                      uses_gas, water_separate, insurance, repairs)
        except (EstimationError, ValueError, KeyError) as exc:
            S.pop(prefix + "results", None)
            st.error(str(exc))

    estimates = S.get(prefix + "results")
    if estimates:
        price = prop.expected_offer_price or prop.asking_price
        context = (prop.postal_code, str(price), kwh, gas, water,
                   uses_gas, water_separate, insurance, repairs)
        if context != S.get(prefix + "context"):
            st.warning("Inputs changed. Click Calculate estimates again before saving.")
            st.stop()

        st.markdown("**3. Review and save to financial scenarios**")
        st.dataframe(pd.DataFrame([{
            "Monthly cost": item.field.replace("_", " ").title(),
            "Estimate (CAD)": float(item.amount), "Calculation": item.basis,
            "Source / caveat": item.source,
        } for item in estimates]), hide_index=True, width='stretch')
        if scenarios:
            st.caption(f"This property has {len(scenarios)} saved financial scenario(s). "
                       "The same monthly cost estimates can be applied to each one.")
            st.dataframe(pd.DataFrame([{
                "Scenario": sc.scenario_name,
                **{e.field.replace("_", " ").title(): fmt(getattr(sc, e.field)) for e in estimates},
            } for sc in scenarios]), hide_index=True, width='stretch')
        else:
            st.info("No scenario exists yet. Saving estimates will create a Default scenario.")
        replace = st.checkbox("Replace existing monthly values in ALL scenarios for this property",
                              value=False, key=prefix + "replace")
        st.caption("Unchecked: fills only empty fields in every scenario. Checked: replaces existing "
                   "values for the estimated fields in every scenario. Other fields are untouched.")
        if st.button("Save estimates to Neon PostgreSQL", type="primary", key=prefix + "save"):
            try:
                if not scenarios:
                    scenarios = [sv.create_scenario(session, prop, rules)]
                count = 0
                for sc in scenarios:
                    for item in estimates:
                        if replace or getattr(sc, item.field) is None:
                            setattr(sc, item.field, item.amount)
                            count += 1
                    sv.recalculate(session, sc, prop, rules)
                session.commit()
                S.pop(prefix + "results", None)
                S.mce_flash = f"Saved {count} monthly-cost field(s) across {len(scenarios)} scenario(s). Financial Calculator will read these saved values."
                st.rerun()
            except Exception as exc:
                session.rollback()
                st.error(f"Could not save estimates: {type(exc).__name__}: {exc}")
