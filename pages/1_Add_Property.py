"""Add Property: paste or upload listing text, review every field, then save.

Nothing here fetches a web page. The listing URL is stored only as a reference link.
"""
import uuid

import pandas as pd
import streamlit as st
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.gate import require_login
from database.connection import get_engine
from database.enums import ListingStatus, PropertyType
from database.models import Property
from parsers import AUTO, SUPPORTED_SITES
from parsers.html_to_text import UploadTooLarge, html_to_text
from schemas.features import MANUAL_ONLY_FIELDS
from services import ingestion as svc
# Labels, layouts and select options are shared with the Property Master page.
from utils.form_config import (  # noqa: E402
    FACT_LAYOUT, FEATURE_LAYOUT, LABELS, MY_LAYOUT, PLACEHOLDERS, SELECTS, friendly_errors,
)

st.set_page_config(page_title="Add Property", page_icon="🏠", layout="wide")
require_login()

S = st.session_state
S.setdefault("stage", "input")
S.setdefault("nonce", 0)


def wkey(name: str) -> str:
    return f"rv_{name}_{S.nonce}"


def reset_to_input(flash: str | None = None) -> None:
    S.nonce += 1
    S.stage = "input"
    for k in ("draft", "values_current", "features_current", "pending", "saved_summary"):
        S.pop(k, None)
    if flash:
        S.flash = flash


def render_field(name: str, current: dict[str, str]) -> None:
    key = wkey(name)
    S.setdefault(key, current.get(name, ""))
    help_text = "Not found on the listing; filled in by you." if name in MANUAL_ONLY_FIELDS else None
    if name in SELECTS:
        options = list(SELECTS[name])
        if S[key] not in options:
            options.append(S[key])
        st.selectbox(LABELS[name], options, key=key, help=help_text)
    else:
        st.text_input(LABELS[name], key=key, help=help_text, placeholder=PLACEHOLDERS.get(name))
    other = S.draft.conflicts.get(name)
    if other and len(other) > 1:
        st.caption("Sources disagree: " + "; ".join(f"{src}: {val or '(blank)'}" for src, val in other))


def render_rows(layout: list[list[str]], current: dict[str, str]) -> None:
    for row in layout:
        cols = st.columns(len(row))
        for col, name in zip(cols, row):
            with col:
                render_field(name, current)


# ============================================================================ stage: input
if S.stage == "input":
    st.title("Add Property")
    if S.get("flash"):
        st.success(S.pop("flash"))
    st.caption(
        "Paste the text of a listing page, or upload a saved .html page. You review and edit every "
        "field before anything is saved. Nothing is fetched from the internet."
    )
    n = S.nonce
    url = st.text_input("Listing URL (optional reference link; the app never opens it)", key=f"in_url_{n}")
    for i in (1, 2):
        title = "Source 1" if i == 1 else "Source 2 (optional: the same property from another site)"
        with st.expander(title, expanded=(i == 1)):
            st.selectbox("Site", [AUTO, *SUPPORTED_SITES], key=f"site_{i}_{n}")
            st.text_area("Paste listing text", height=220, key=f"paste_{i}_{n}")
            st.file_uploader("...or upload a saved page (.html)", type=["html", "htm"], key=f"up_{i}_{n}")

    c1, c2, _ = st.columns([1, 1, 3])
    go = c1.button("Extract and review", type="primary", key=f"go_{n}")
    manual = c2.button("Enter manually", key=f"manual_{n}")

    if manual:
        S.draft = svc.Draft(reference_url=url.strip() or None)
        S.values_current = {k: "" for k in svc.PROPERTY_FIELDS}
        S.features_current = {k: "" for k in svc.FEATURE_FIELDS}
        S.initial_values, S.initial_features = dict(S.values_current), dict(S.features_current)
        S.stage = "review"
        st.rerun()

    if go:
        slots, problems = [], []
        for i in (1, 2):
            pasted = (S.get(f"paste_{i}_{n}") or "").strip()
            upload = S.get(f"up_{i}_{n}")
            site = S.get(f"site_{i}_{n}", AUTO)
            if pasted and upload:
                problems.append(f"Source {i}: use either pasted text or an uploaded file, not both.")
            elif upload:
                try:
                    slots.append(svc.SourceInput(html_to_text(upload.getvalue()), site, True, upload.name))
                except UploadTooLarge as exc:
                    problems.append(f"Source {i}: {exc}")
            elif pasted:
                slots.append(svc.SourceInput(pasted, site))
        if problems:
            for p in problems:
                st.error(p)
        elif not slots:
            st.warning("Paste some listing text or upload a page first, or choose Enter manually.")
        else:
            draft = svc.extract_draft(slots, url)
            try:
                with Session(get_engine()) as session:
                    draft.log_id = svc.log_extraction(session, draft)
                    session.commit()
            except Exception as exc:  # noqa: BLE001  (logging must never block the workflow)
                st.warning(f"Could not write the extraction log ({type(exc).__name__}). Continuing.")
            if draft.rejected_reason:
                st.error(f"This listing was rejected: {draft.rejected_reason}")
            else:
                S.draft = draft
                S.values_current = dict(draft.values) or {k: "" for k in svc.PROPERTY_FIELDS}
                S.features_current = dict(draft.features) or {k: "" for k in svc.FEATURE_FIELDS}
                S.initial_values = dict(S.values_current)
                S.initial_features = dict(S.features_current)
                S.stage = "review"
                st.rerun()

# ============================================================================ stage: review
elif S.stage == "review":
    draft: svc.Draft = S.draft
    st.title("Review and save")
    if S.get("error_msg"):
        st.error(S.pop("error_msg"))
    if draft.sources:
        st.info(f"Extracted from: {', '.join(draft.sources)}. Check every field; nothing is saved until you press Save.")
    else:
        st.info("Manual entry: nothing was extracted. Fill in what you know.")
    for note in dict.fromkeys(draft.notes):
        st.warning(note)
    if draft.reference_url:
        st.caption(f"Reference link (not fetched): {draft.reference_url}")

    with st.form(f"review_form_{S.nonce}"):
        st.subheader("Listing facts")
        render_rows(FACT_LAYOUT, S.values_current)
        render_rows(MY_LAYOUT, S.values_current)
        desc_key = wkey("description")
        S.setdefault(desc_key, S.values_current.get("description", ""))
        st.text_area("Description (kept for verification)", key=desc_key, height=160)
        with st.expander("Features and your own observations", expanded=False):
            fkey = wkey("property_features")
            S.setdefault(fkey, S.features_current.get("property_features", ""))
            st.text_input(LABELS["property_features"], key=fkey)
            render_rows(FEATURE_LAYOUT, S.features_current)
        import_history = False
        if draft.history_rows:
            st.subheader("Listing history found")
            st.dataframe(
                pd.DataFrame([
                    {"Date": r["observed_on"], "Price": float(r["asking_price"]) if r["asking_price"] else None,
                     "Event": r["event"], "Listing ID": r["listing_id"]} for r in draft.history_rows
                ]),
                hide_index=True,
            )
            import_history = st.checkbox(
                f"Import these {len(draft.history_rows)} rows into this property's history",
                value=True, key=f"hist_{S.nonce}",
            )
        b1, b2, b3, _ = st.columns([1, 1.4, 1, 3])
        save = b1.form_submit_button("Save", type="primary")
        save_more = b2.form_submit_button("Save and add another")
        back = b3.form_submit_button("Start over")

    if back:
        reset_to_input()
        st.rerun()

    if save or save_more:
        vals = {k: S.get(wkey(k), "") for k in svc.PROPERTY_FIELDS if k != "description"}
        vals["description"] = S.get(desc_key, "")
        feats = {k: S.get(wkey(k), "") for k in svc.FEATURE_FIELDS}
        S.values_current, S.features_current = dict(vals), dict(feats)
        missing = svc.missing_required(vals)
        if missing:
            st.error("Required: " + ", ".join(missing))
        else:
            try:
                data, feat_models = svc.build_models(vals, feats, draft.reference_url)
            except ValidationError as exc:
                st.error("Please fix these fields:")
                for line in friendly_errors(exc):
                    st.write(f"- {line}")
            else:
                try:
                    with Session(get_engine()) as session:
                        matches = svc.duplicates_for(session, data)
                        match_ids = [str(m.property.id) for m in matches]
                        reasons = {str(m.property.id): m.reasons for m in matches}
                    S.pending = dict(data=data, feats=feat_models, vals=vals, feats_text=feats,
                                     import_history=import_history, another=bool(save_more),
                                     match_ids=match_ids, reasons=reasons)
                    S.stage = "duplicate" if match_ids else "commit"
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not reach the database ({type(exc).__name__}). Nothing was saved.")

# ============================================================================ stage: commit (no duplicate)
elif S.stage == "commit":
    p, draft = S.pending, S.draft
    try:
        with Session(get_engine()) as session:
            prop = svc.save_new(
                session, draft, p["data"], p["feats"], S.initial_values, p["vals"],
                S.initial_features, p["feats_text"], import_history=p["import_history"],
            )
            label = f"{prop.unit + ' - ' if prop.unit else ''}{prop.address}, {prop.city}"
            session.commit()
    except IntegrityError:
        S.error_msg = "The database rejected this record as a duplicate. Nothing was saved."
        S.stage = "review"
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        S.error_msg = f"Could not save ({type(exc).__name__}: {exc}). Nothing was saved."
        S.stage = "review"
        st.rerun()
    if p["another"]:
        reset_to_input(f"Saved: {label}. Ready for the next property.")
    else:
        S.saved_summary = f"Saved: {label}."
        S.stage = "saved"
    st.rerun()

# ============================================================================ stage: duplicate
elif S.stage == "duplicate":
    p, draft = S.pending, S.draft
    st.title("This property may already exist")
    with Session(get_engine()) as session:
        ids = [uuid.UUID(i) for i in p["match_ids"]]
        existing = {str(x.id): x for x in session.scalars(select(Property).where(Property.id.in_(ids)))}
        names = {i: f"{(e.unit + ' - ') if e.unit else ''}{e.address}, {e.city} "
                    f"(MLS {e.listing_number or 'n/a'}; matched on {', '.join(p['reasons'][i])})"
                 for i, e in existing.items()}
        chosen_id = p["match_ids"][0]
        if len(p["match_ids"]) > 1:
            chosen_id = st.radio("Several records match. Which one do you mean?", p["match_ids"],
                                 format_func=lambda i: names[i])
        else:
            st.write(f"Matched: {names[chosen_id]}")
        e, d = existing[chosen_id], p["data"]
        rows = []
        for label, attr in [("MLS number", "listing_number"), ("Address", "address"), ("Unit", "unit"),
                            ("Postal code", "postal_code"), ("Listing status", "listing_status"),
                            ("Asking price", "asking_price"), ("Maintenance fee", "maintenance_fee"),
                            ("Bedrooms", "bedrooms"), ("Bathrooms", "bathrooms")]:
            rows.append({"Field": label, "Existing record": svc.to_text(getattr(e, attr)),
                         "New data": svc.to_text(getattr(d, attr))})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        st.caption("Updating keeps your hand-edited fields, adds a new history row, and never deletes history.")
        c1, c2, _ = st.columns([1.2, 1, 3])
        if c1.button("Update existing record", type="primary"):
            try:
                prop = session.get(Property, e.id)
                svc.update_existing(session, prop, draft, d, p["feats"], S.initial_features,
                                    p["feats_text"], import_history=p["import_history"])
                label = f"{prop.unit + ' - ' if prop.unit else ''}{prop.address}, {prop.city}"
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                st.error(f"Could not update ({type(exc).__name__}: {exc}).")
            else:
                if p["another"]:
                    reset_to_input(f"Updated: {label}. Ready for the next property.")
                else:
                    S.saved_summary = f"Updated: {label}."
                    S.stage = "saved"
                st.rerun()
        if c2.button("Cancel"):
            S.stage = "review"
            st.rerun()

# ============================================================================ stage: saved
elif S.stage == "saved":
    st.title("Add Property")
    st.success(S.get("saved_summary", "Saved."))
    if st.button("Add another property", type="primary"):
        reset_to_input()
        st.rerun()
