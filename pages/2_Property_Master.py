"""Property Master: browse, search, filter, sort, edit, delete and export saved properties.

Pick a property by clicking its row in the table (or from the list under the table). The
details, edit form and delete confirmation open below. Business rules live in
services/property_master.py, services/property_edit.py and services/export_service.py.
"""
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from auth.gate import require_login
from database import repository as repo
from database.connection import get_engine
from services import export_service as exp
from services import ingestion as svc
from services import property_edit as edit
from services import property_master as pm
from utils.form_config import FACT_LAYOUT, FEATURE_LAYOUT, LABELS, MY_LAYOUT, PLACEHOLDERS, SELECTS

st.set_page_config(page_title="Property Master", page_icon="📋", layout="wide")
require_login()

S = st.session_state
S.setdefault("pm_nonce", 0)       # bump to reset the table selection and the picker
S.setdefault("pm_edit_nonce", 0)  # bump after a save so the edit form reloads from the database

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CENTS = {"maintenance_fee", "listed_annual_property_tax", "monthly_outgoing", "cash_required"}


# ============================================================================ helpers
def row_label(row: dict) -> str:
    unit = f", unit {row['unit']}" if row.get("unit") else ""
    return f"{row['address']}{unit} - {row['city']}"


def column_config(keys: list[str]) -> dict:
    cfg = {}
    for k in keys:
        c = pm.BY_KEY[k]
        if c.kind == "money":
            cfg[k] = st.column_config.NumberColumn(c.label, help=c.help, format="$%.2f" if k in CENTS else "$%d")
        elif c.kind in ("int", "pct"):
            cfg[k] = st.column_config.NumberColumn(c.label, help=c.help, format="%d")
        elif c.kind == "datetime":
            cfg[k] = st.column_config.DatetimeColumn(c.label, help=c.help, format="YYYY-MM-DD HH:mm")
        elif c.kind == "link":
            cfg[k] = st.column_config.LinkColumn(c.label, help=c.help)
        else:
            cfg[k] = st.column_config.TextColumn(c.label, help=c.help)
    return cfg


def render_filters(df: pd.DataFrame) -> pm.Filters:
    with st.expander("Search and filters", expanded=True):
        text = st.text_input(
            "Search", key="pm_text", placeholder="Address, unit, MLS number, neighbourhood, city or postal code",
        )
        a, b, c, d = st.columns(4)
        cities = a.multiselect("City", pm.distinct(df, "city"), key="pm_city")
        hoods = b.multiselect("Neighbourhood", pm.distinct(df, "neighbourhood"), key="pm_hood")
        types = c.multiselect("Property type", pm.distinct(df, "property_type"), key="pm_type")
        statuses = d.multiselect("Listing status", pm.distinct(df, "listing_status"), key="pm_status")
        e, f, g, h = st.columns(4)
        price_min = e.number_input("Min price", min_value=0.0, value=None, step=10000.0, format="%.0f", key="pm_pmin")
        price_max = f.number_input("Max price", min_value=0.0, value=None, step=10000.0, format="%.0f", key="pm_pmax")
        fee_min = g.number_input("Min maintenance fee", min_value=0.0, value=None, step=50.0, format="%.0f", key="pm_fmin")
        fee_max = h.number_input("Max maintenance fee", min_value=0.0, value=None, step=50.0, format="%.0f", key="pm_fmax")
        i, j, k, m = st.columns(4)
        beds = i.number_input("Min bedrooms", min_value=0, max_value=30, value=None, step=1, key="pm_beds")
        baths = j.number_input("Min bathrooms", min_value=0, max_value=30, value=None, step=1, key="pm_baths")
        ext = k.multiselect("Extraction status", pm.distinct(df, "extraction_status"), key="pm_ext")
        m.write("")
        only_missing = m.checkbox("Only records with missing fields", key="pm_missing")
    return pm.Filters(
        text=text, cities=cities, neighbourhoods=hoods, property_types=types, statuses=statuses,
        extraction_statuses=ext, price_min=price_min, price_max=price_max, fee_min=fee_min, fee_max=fee_max,
        bedrooms_min=beds, bathrooms_min=baths, only_missing=only_missing,
    )


# ============================================================================ selected property
def render_details(row: dict, prop) -> None:
    pairs = pm.detail_pairs(row)
    left, right = st.columns(2)
    for column, titles in ((left, ["Listing", "Features"]), (right, ["Your entries", "Data quality"])):
        with column:
            for title in titles:
                st.markdown(f"**{title}**")
                st.dataframe(pd.DataFrame(pairs[title], columns=["Field", "Value"]), hide_index=True)
    st.markdown("**Listing history**")
    if prop.history:
        hist = exp.history_frame(sorted(prop.history, key=lambda h: h.observed_at), {str(prop.id): (row_label(row), prop.listing_number)})
        st.dataframe(
            hist.drop(columns=["Property", "MLS number"]), hide_index=True,
            column_config={"Asking price": st.column_config.NumberColumn(format="$%d")},
        )
    else:
        st.caption("No listing history saved for this property.")
    st.text_area("Description", value=prop.description or "", height=200, disabled=True, key=f"det_desc_{prop.id}")


def edit_field(name: str, current: dict[str, str], prefix: str) -> None:
    key = f"{prefix}_{name}"
    S.setdefault(key, current.get(name, ""))
    if name in SELECTS:
        options = list(SELECTS[name])
        if S[key] not in options:
            options.append(S[key])
        st.selectbox(LABELS[name], options, key=key)
    else:
        st.text_input(LABELS[name], key=key, placeholder=PLACEHOLDERS.get(name))


def edit_rows(layout: list[list[str]], current: dict[str, str], prefix: str) -> None:
    for row in layout:
        for col, name in zip(st.columns(len(row)), row):
            with col:
                edit_field(name, current, prefix)


def render_edit(session: Session, prop) -> None:
    init_vals, init_feats = edit.form_values(prop)
    prefix = f"ed_{prop.id}_{S.pm_edit_nonce}"
    S.setdefault(f"{prefix}_listing_url", prop.listing_url or "")
    S.setdefault(f"{prefix}_description", init_vals.get("description", ""))

    st.caption(
        "Change any field and save. Blank boxes are saved as blank (cleared). Everything you change is "
        "remembered as your own entry, so a later listing update never overwrites it. Saving marks the "
        "record as verified today."
    )
    with st.form(f"edit_form_{prop.id}_{S.pm_edit_nonce}"):
        st.markdown("**Listing facts**")
        edit_rows(FACT_LAYOUT, init_vals, prefix)
        st.text_input(LABELS["listing_url"], key=f"{prefix}_listing_url")
        st.markdown("**Your entries**")
        edit_rows(MY_LAYOUT, init_vals, prefix)
        st.markdown("**Features**")
        edit_field("property_features", init_feats, prefix)
        edit_rows(FEATURE_LAYOUT, init_feats, prefix)
        st.text_area(LABELS["description"], key=f"{prefix}_description", height=200)
        submitted = st.form_submit_button("Save changes", type="primary")

    if not submitted:
        return
    final_vals = {k: S[f"{prefix}_{k}"] for k in svc.PROPERTY_FIELDS}
    final_feats = {k: S[f"{prefix}_{k}"] for k in svc.FEATURE_FIELDS}
    check = edit.check_edit(session, prop, final_vals, final_feats, S[f"{prefix}_listing_url"])
    errors = check.errors if not check.ok else edit.save_edit(
        session, prop, check, (init_vals, init_feats), (final_vals, final_feats)
    )
    if errors:
        session.rollback()
        st.error("Nothing was saved. Please fix:\n\n" + "\n".join(f"- {e}" for e in errors))
        return
    session.commit()
    S.pm_flash = f"Saved changes to {prop.address}."
    S.pm_edit_nonce += 1
    st.rerun()


def render_delete(session: Session, prop) -> None:
    summary = edit.delete_summary(prop)
    st.warning(
        f"This permanently deletes **{prop.address}**, its features, "
        f"{summary['history_rows']} listing history row(s) and {summary['financial_scenarios']} financial "
        f"scenario(s). {summary['logs_kept']} extraction log entry(ies) are kept, with the link to this "
        "property removed."
    )
    agreed = st.checkbox("I understand this cannot be undone", key=f"del_ok_{prop.id}")
    if st.button("Delete permanently", type="primary", disabled=not agreed, key=f"del_btn_{prop.id}"):
        address = prop.address
        edit.delete(session, prop)
        session.commit()
        S.pm_flash = f"Deleted {address}."
        S.pm_nonce += 1
        S.pm_last_table_sel = None
        st.rerun()


def render_selected(selected_id: str, row: dict) -> None:
    with Session(get_engine()) as session:
        prop = repo.get_property(session, uuid.UUID(selected_id))
        if prop is None:
            st.warning("This property no longer exists.")
            return
        st.subheader(row_label(row))
        details_tab, edit_tab, delete_tab = st.tabs(["Details", "Edit", "Delete"])
        with details_tab:
            render_details(row, prop)
        with edit_tab:
            render_edit(session, prop)
        with delete_tab:
            render_delete(session, prop)


# ============================================================================ page
st.title("Property Master")
if S.get("pm_flash"):
    st.success(S.pop("pm_flash"))

try:
    with Session(get_engine()) as _session:
        rows = [pm.property_row(p) for p in repo.list_properties(_session)]
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not read the database: {type(exc).__name__}. Check the connection and try again.")
    st.stop()

if not rows:
    st.info("No properties saved yet. Use Add Property in the sidebar to save your first one.")
    st.stop()

df_all = pm.build_frame(rows)
by_id = {r["_id"]: r for r in rows}
filters = render_filters(df_all)
view = pm.apply_filters(df_all, filters)

labels = {c.key: c.label for c in pm.TABLE_COLUMNS}
s1, s2, s3 = st.columns([2, 1, 3])
sort_key = s1.selectbox("Sort by", list(labels), index=list(labels).index("date_added"), format_func=labels.get, key="pm_sort")
descending = s2.radio("Order", ["Descending", "Ascending"], horizontal=True, key="pm_order") == "Descending"
chosen = s3.multiselect("Columns shown", list(labels), default=pm.DEFAULT_COLUMNS, format_func=labels.get, key="pm_cols")
cols = [k for k in labels if k in chosen] or pm.DEFAULT_COLUMNS
view = pm.sort_frame(view, sort_key, ascending=not descending)

caption = f"Showing {len(view)} of {len(df_all)} properties. Times are Toronto time. Click a column header to sort."
if filters.numeric_active():
    caption += " Properties with a blank value are left out while a price, fee, bedroom or bathroom filter is on."
st.caption(caption)

event = st.dataframe(
    view[cols], hide_index=True, column_config=column_config(cols),
    on_select="rerun", selection_mode="single-row", key=f"pm_table_{S.pm_nonce}",
)
picked_rows = event.selection.rows
table_id = view.iloc[picked_rows[0]]["_id"] if picked_rows and picked_rows[0] < len(view) else None
pick_key = f"pm_pick_{S.pm_nonce}"
if table_id != S.get("pm_last_table_sel"):  # a new click in the table drives the picker below
    S.pm_last_table_sel = table_id
    if table_id:
        S[pick_key] = table_id

options = [""] + sorted(by_id, key=lambda i: row_label(by_id[i]).lower())
selected_id = st.selectbox(
    "Open a property (click a row above, or choose here)", options, key=pick_key,
    format_func=lambda i: "-" if not i else row_label(by_id[i]),
)

if selected_id:
    render_selected(selected_id, by_id[selected_id])

# ---------------------------------------------------------------------------- export
with st.expander("Export"):
    include_description = st.checkbox("Include the description text (the agent's wording)", key="pm_exp_desc")
    include_history = st.checkbox("Add a second sheet with listing history (Excel only)", key="pm_exp_hist")
    export_cols = cols + (["description"] if include_description else [])
    st.caption(
        f"Exports the {len(view)} property(ies) currently shown, with the columns and sort order above. "
        "Files download through your browser; nothing is saved on the server."
    )
    history_df = None
    if include_history and len(view):
        with Session(get_engine()) as _session:
            history_rows = repo.history_for_properties(_session, [uuid.UUID(i) for i in view["_id"]])
        history_df = exp.history_frame(history_rows, {i: (row_label(r), r["listing_number"]) for i, r in by_id.items()})
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    empty = len(view) == 0
    st.download_button(
        "Download Excel (.xlsx)", exp.build_xlsx(view, export_cols, history_df) if not empty else b"",
        file_name=f"properties-{stamp}.xlsx", mime=XLSX_MIME, disabled=empty,
    )
    st.download_button(
        "Download CSV", exp.build_csv(view, export_cols) if not empty else b"",
        file_name=f"properties-{stamp}.csv", mime="text/csv", disabled=empty,
    )

# ---------------------------------------------------------------------------- maintenance
with st.expander("Maintenance"):
    st.caption(
        "Data quality is always calculated from the current values in the table. Properties saved before "
        "this page existed have no stored score yet; this button stores one for every property."
    )
    if st.button("Recalculate stored data-quality scores"):
        with Session(get_engine()) as _session:
            count = repo.recalculate_all_quality(_session)
            _session.commit()
        S.pm_flash = f"Recalculated data quality for {count} property(ies)."
        st.rerun()