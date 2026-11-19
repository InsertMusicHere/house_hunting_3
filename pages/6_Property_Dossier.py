"""Phase 6f: read-only property dossier and side-by-side comparison."""
import streamlit as st
import pandas as pd
from sqlalchemy.orm import Session
from auth.gate import require_login
from database.connection import get_engine
from services.research.dossier import build_dossiers

st.set_page_config(page_title="Property Dossier & Comparison", page_icon="🏠", layout="wide")
require_login()
st.title("Property Dossier & Comparison")
st.caption("Saved Neon data only. No new API requests, PDF export or automatic research refresh.")

def money(v):
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"
    except (ValueError, TypeError):
        return "—"

def name(d):
    return ", ".join(str(x) for x in (d["address"], d["unit"], d["city"]) if x)

def value(v):
    return "—" if v is None else str(v)

with Session(get_engine()) as session:
    dossiers = build_dossiers(session)
    if not dossiers:
        st.info("Add properties in Property Master to get started.")
        st.stop()

    labels = {d["id"]: name(d) for d in dossiers}
    ids = st.multiselect(
        "Select 2–5 properties to compare",
        options=list(labels), default=list(labels)[:min(3, len(labels))],
        format_func=lambda x: labels[x], max_selections=5,
    )
    selected = [d for d in dossiers if d["id"] in ids]
    if len(selected) < 2:
        st.info("Select at least two properties to compare.")

    st.subheader("Side-by-side comparison")
    if selected:
        def metric(d, key):
            f, p, c = d["financial"], d["police"], d["convenience"]
            e, t = d["essentials"], d["transit"]
            mapping = {
                "City": d["city"], "Asking price": money(d["asking_price"]),
                "Bedrooms": d["bedrooms"], "Bathrooms": d["bathrooms"],
                "Primary scenario": f["scenario"], "Financial status": (
                    "Out of date" if f["out_of_date"] else f["status"]),
                "Monthly outgoing": money(f["monthly_outgoing"]),
                "Cash required": money(f["cash_required"]),
                "Mortgage payment": money(f["mortgage_payment"]),
                "Convenience score": f"{c['score']:.1f}/100" if c and c["score"] is not None else None,
                "Essential-services research": "Recorded" if e else None,
                "GTFS scheduled departures": t["departures"] if t else None,
                "Police geographic scope": p["scope"] if p else None,
                "Police reporting year": p["year"] if p else None,
                "TPS relative index": f"{p['score']}/100" if p and p["score"] is not None else None,
                "Owner verification notes": sum(len(x) for x in d["notes"].values()),
            }
            return value(mapping.get(key))
        rows = ["City", "Asking price", "Bedrooms", "Bathrooms", "Primary scenario",
                "Financial status", "Monthly outgoing", "Cash required", "Mortgage payment",
                "Convenience score", "Essential-services research", "GTFS scheduled departures",
                "Police geographic scope", "Police reporting year", "TPS relative index",
                "Owner verification notes"]
        st.dataframe(pd.DataFrame({"Metric": rows, **{
            labels[d["id"]]: [metric(d, k) for k in rows] for d in selected
        }}), hide_index=True, width="stretch")
        st.caption("Toronto's relative police index is only comparable within TPS neighbourhoods. "
                   "Peel-wide counts are not property-level evidence; missing values are not zero.")

    st.divider()
    st.subheader("Individual property dossier")
    chosen = st.selectbox("Property", list(labels), format_func=lambda x: labels[x])
    d = next(x for x in dossiers if x["id"] == chosen)
    f, c, e, t, p = (d[k] for k in ("financial", "convenience", "essentials", "transit", "police"))
    a,b,c3 = st.columns(3)
    a.metric("Asking price", money(d["asking_price"]))
    b.metric("Monthly outgoing (saved scenario)", money(f["monthly_outgoing"]))
    c3.metric("Cash required (saved scenario)", money(f["cash_required"]))
    st.write(f"**{name(d)}** · {value(d['property_type'])} · "
             f"{value(d['bedrooms'])} bed · {value(d['bathrooms'])} bath")
    st.caption("Location verified" if d["location_verified"] else "Location not verified")
    if d["listing_url"]:
        st.markdown(f"[Original listing]({d['listing_url']})")

    with st.expander("Financial scenario", expanded=True):
        st.write(f"Primary scenario: **{value(f['scenario'])}** · Status: **{f['status']}**")
        if f["out_of_date"]:
            st.warning("Saved financial calculation is out of date relative to current inputs/rules. "
                       "Recalculate in Financial Calculator before relying on these amounts.")
            for reason in f["out_of_date_reasons"]:
                st.caption(str(reason))
        st.write("Purchase price:", money(f["purchase_price"]))
        st.write("Monthly mortgage:", money(f["mortgage_payment"]))
        st.write("Monthly outgoing:", money(f["monthly_outgoing"]))
        st.write("Cash required:", money(f["cash_required"]))
        if f["status"] != "ok":
            st.info("No successful saved calculation. Use Financial Calculator to save a primary scenario.")

    with st.expander("Convenience and essential services", expanded=True):
        if c:
            st.metric("Saved convenience score", f"{c['score']:.1f}/100" if c["score"] is not None else "—")
            st.caption(f"Source: {value(c['source'])} · Recorded: {c['run_at']}")
        else:
            st.info("No saved convenience research for this verified location.")
        if e:
            st.write("Saved essential-service metrics")
            st.dataframe([{"Metric": k, "Saved value": str(v)} for k,v in e["metrics"].items()],
                         hide_index=True, width="stretch")
            st.caption(f"Source: {value(e['metadata']['source'])} · Recorded: {e['metadata']['run_at']}")
        else:
            st.info("No saved essential-services summary for this verified location.")
        if t:
            st.write(f"GTFS scheduled stop departures: **{value(t['departures'])}**")
            st.caption(f"Schedule date: {value(t['schedule_date'])}; window: {value(t['window'])}")
        else:
            st.caption("No saved GTFS analysis for this verified location.")

    with st.expander("Police statistics", expanded=True):
        if p:
            st.write(f"**{value(p['area'])}** · {p['scope']} · {value(p['year'])}")
            if p["score"] is not None:
                st.metric("TPS relative reported-crime index (not a safety score)", f"{p['score']}/100")
            else:
                st.info("No comparable local relative police index is available.")
            st.dataframe([{"Indicator": k, "Reported count": v} for k,v in p["counts"].items()],
                         hide_index=True, width="stretch")
            st.caption(p["note"] or "")
            if p["source_url"]:
                st.markdown(f"[Police data source]({p['source_url']})")
        else:
            st.info("No supported saved police snapshot for this verified location.")

    with st.expander("Owner-verified notes and due diligence"):
        for category, entries in d["notes"].items():
            st.markdown(f"**{category.replace('_',' ').title()}**")
            if not entries:
                st.caption("No saved entries")
            for entry in entries[:15]:
                st.write(entry["details"])
                if entry["source_url"]:
                    st.markdown(f"[Supporting source]({entry['source_url']})")
                st.caption(f"Recorded: {entry['recorded_at'] or 'unknown'}")

st.caption("Research is descriptive and source-dependent. Missing research is not evidence "
           "of an absent service or risk. Financial amounts are saved estimates, not lender quotes.")
