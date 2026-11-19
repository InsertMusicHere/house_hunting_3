"""Editing a saved property by hand.

Checks: 1. required fields, 2. column length limits, 3. field validation (same rules as Add
Property). Problems from 1-3 are reported together. Once those pass, 4. collision with another
saved property (MLS number, address + unit + postal code, or link) is checked. Nothing is written
unless all four pass. No page code here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import repository as repo
from database.models import Property, PropertyFeatures
from schemas.features import FeaturesInput
from schemas.property import PropertyExtract
from services.ingestion import (
    FEATURE_FIELDS,
    PROPERTY_FIELDS,
    build_models,
    duplicates_for,
    manual_overrides,
    to_text,
)
from utils.form_config import LABELS, friendly_errors

REQUIRED = ("address", "city")


@dataclass
class EditCheck:
    data: PropertyExtract | None = None
    feats: FeaturesInput | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.data is not None


def form_values(prop: Property) -> tuple[dict[str, str], dict[str, str]]:
    """Stored values as the text shown in the edit form (property fields, feature fields)."""
    values = {k: to_text(getattr(prop, k)) for k in PROPERTY_FIELDS}
    feats = {
        k: to_text(getattr(prop.features, k)) if prop.features is not None else ""
        for k in FEATURE_FIELDS
    }
    return values, feats


def length_errors(values: dict[str, str], feats: dict[str, str]) -> list[str]:
    """Text longer than its database column would be rejected on save; say so up front."""
    out = []
    for name, text in {**values, **feats}.items():
        text = (text or "").strip()
        col = Property.__table__.columns.get(name)
        if col is None:
            col = PropertyFeatures.__table__.columns.get(name)
        limit = getattr(getattr(col, "type", None), "length", None)
        if limit and len(text) > limit:
            out.append(f"{LABELS.get(name, name)} is {len(text)} characters; the limit is {limit}.")
    return out


def check_edit(
    session: Session, prop: Property, values: dict[str, str], feats: dict[str, str], reference_url: str,
) -> EditCheck:
    check = EditCheck()
    for name in REQUIRED:
        if not (values.get(name) or "").strip():
            check.errors.append(f"{LABELS[name].rstrip(' *')} is required.")
    check.errors += length_errors(values, feats)
    if (reference_url or "").strip() and len(reference_url.strip()) > 1000:
        check.errors.append("Reference link is too long (limit 1000 characters).")

    try:
        data, feat_model = build_models(values, feats, reference_url)
    except ValidationError as exc:
        check.errors += friendly_errors(exc)
        return check
    if check.errors:  # required / length problems found above; field rules passed
        return check
    check.data, check.feats = data, feat_model

    reason_text = {"listing_number": "MLS number", "address": "address, unit and postal code", "url": "reference link"}
    for match in duplicates_for(session, check.data):
        if match.property.id != prop.id:
            why = " and ".join(reason_text.get(r, r) for r in match.reasons)
            check.errors.append(
                f"This would duplicate another saved property ({match.property.address}): same {why}."
            )
    return check


def save_edit(
    session: Session,
    prop: Property,
    check: EditCheck,
    initial: tuple[dict[str, str], dict[str, str]],
    final: tuple[dict[str, str], dict[str, str]],
) -> list[str]:
    """Persist a validated edit. Returns error messages (empty on success).

    Every field whose text differs from `initial` is recorded as a manual change, including
    fields you cleared on purpose, so later listing updates never put the old value back.
    """
    if not check.ok:
        return check.errors or ["Nothing to save."]
    (init_vals, init_feats), (fin_vals, fin_feats) = initial, final
    overrides = manual_overrides(init_vals, fin_vals)
    feature_changes = {
        k: (fin_feats.get(k) or "").strip()
        for k in FEATURE_FIELDS
        if (init_feats.get(k) or "").strip() != (fin_feats.get(k) or "").strip()
    }
    try:
        with session.begin_nested():  # a database-level collision rolls back only this edit
            repo.update_property_manually(
                session, prop, check.data, check.feats,
                property_fields=list(PROPERTY_FIELDS), feature_fields=list(FEATURE_FIELDS),
                overrides=overrides, feature_changes=feature_changes,
            )
    except IntegrityError:
        session.expire(prop)
        return ["Another saved property already uses this MLS number or address. Nothing was changed."]
    return []


def delete_summary(prop: Property) -> dict[str, int]:
    """What deleting would remove or keep, for the confirmation step."""
    return {
        "history_rows": len(prop.history),
        "financial_scenarios": len(prop.financials),
        "logs_kept": len(prop.logs),
    }


def delete(session: Session, prop: Property) -> dict[str, Any]:
    return repo.delete_property(session, prop)