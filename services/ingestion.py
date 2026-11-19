"""Turns pasted/uploaded listing text into a reviewable draft, and saves reviewed drafts.

No page code lives here and nothing is fetched from the internet.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.enums import IngestionMethod, ListingStatus, LogStatus, PropertyType
from database.models import DataSource, ExtractionLog, Property
from database.repository import (
    create_property, fill_missing_features, find_duplicates, update_property_from_new_listing,
)
from parsers import AUTO, parse_text
from parsers.common import ParsedListing
from schemas.features import TEXT_FIELDS, FeaturesInput
from schemas.property import TRACKED_FIELDS, PropertyExtract

# Realtor.ca is listed first: for facts it carries the more complete and more precise values.
SOURCE_PRIORITY = ["Realtor.ca", "HouseSigma"]

PROPERTY_FIELDS = [
    "listing_number", "listing_status", "listing_date", "address", "unit", "city", "neighbourhood",
    "postal_code", "property_type", "asking_price", "bedrooms", "bedrooms_extra", "bathrooms",
    "interior_size_min", "interior_size_max", "year_built", "floor", "parking", "parking_spaces",
    "locker", "maintenance_fee", "listed_annual_property_tax", "listed_tax_year", "description",
    "direction", "expected_offer_price",
]
FEATURE_FIELDS = ["property_features", *TEXT_FIELDS]
REQUIRED_FIELDS = {"address": "Street address", "city": "City"}


@dataclass
class SourceInput:
    text: str
    site: str = AUTO
    from_upload: bool = False
    filename: str | None = None


@dataclass
class Draft:
    values: dict[str, str] = field(default_factory=dict)          # property fields as form text
    features: dict[str, str] = field(default_factory=dict)        # feature fields as form text
    conflicts: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    history_rows: list[dict] = field(default_factory=list)
    raw_extraction: dict | None = None
    ingestion_method: str = IngestionMethod.MANUAL_ENTRY.value
    sources: list[str] = field(default_factory=list)
    reference_url: str | None = None
    fields_extracted: list[str] = field(default_factory=list)
    fields_missing: list[str] = field(default_factory=list)
    rejected_reason: str | None = None
    unrecognized: int = 0
    duration_ms: int = 0
    log_id: str | None = None


# --------------------------------------------------------------------------- helpers
def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Decimal):
        return f"{value:f}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return a == b


def _json_default(o: Any):
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=_json_default))


def _ordered(parsed: list[ParsedListing]) -> list[ParsedListing]:
    def rank(p: ParsedListing) -> int:
        return SOURCE_PRIORITY.index(p.source) if p.source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
    return sorted(parsed, key=rank)


def _merge(parsed: list[ParsedListing], attr: str, names: list[str]):
    """First source in priority order wins; every disagreeing candidate is remembered."""
    candidates: dict[str, list[tuple[str, Any]]] = {}
    for p in parsed:
        for key, value in getattr(p, attr).items():
            if value is None or key not in names:
                continue
            candidates.setdefault(key, []).append((p.source, value))
    chosen, conflicts = {}, {}
    for key, cands in candidates.items():
        if key == "property_features":
            merged: list[str] = []
            for _, lst in cands:
                for item in lst:
                    if item.casefold() not in {m.casefold() for m in merged}:
                        merged.append(item)
            chosen[key] = merged
            continue
        chosen[key] = cands[0][1]
        distinct = []
        for _, v in cands:
            if not any(_same(v, d) for d in distinct):
                distinct.append(v)
        if len(distinct) > 1:
            conflicts[key] = [(src, to_text(v)) for src, v in cands]
    return chosen, conflicts


# --------------------------------------------------------------------------- extraction
def extract_draft(sources: list[SourceInput], listing_url: str | None = None) -> Draft:
    """Parse every non-empty source and merge them into one editable draft."""
    started = time.monotonic()
    draft = Draft(reference_url=(listing_url or "").strip() or None)
    texts = [s for s in sources if s.text and s.text.strip()]
    parsed: list[ParsedListing] = []
    for s in texts:
        result = parse_text(s.text, s.site)
        if result is None:
            draft.unrecognized += 1
            draft.notes.append(
                "A source was not recognized as HouseSigma or Realtor.ca text, so nothing was "
                "extracted from it. Fill the fields in by hand, or send me a sample from that site "
                "to add support."
            )
            continue
        parsed.append(result)
        draft.notes += result.notes
        if result.out_of_scope:
            draft.rejected_reason = result.out_of_scope

    draft.sources = [p.source for p in parsed]
    if draft.rejected_reason:
        draft.duration_ms = int((time.monotonic() - started) * 1000)
        return draft

    parsed = _ordered(parsed)
    chosen, conflicts = _merge(parsed, "fields", PROPERTY_FIELDS)
    feat_chosen, feat_conflicts = _merge(parsed, "features", FEATURE_FIELDS)
    draft.values = {k: to_text(chosen.get(k)) for k in PROPERTY_FIELDS}
    draft.features = {k: to_text(feat_chosen.get(k)) for k in FEATURE_FIELDS}
    draft.conflicts = {**conflicts, **feat_conflicts}
    draft.history_rows = [r for p in parsed for r in p.history]
    draft.fields_extracted = sorted(k for k, v in chosen.items() if v not in (None, ""))
    draft.fields_missing = [f for f in TRACKED_FIELDS if not draft.values.get(f)]

    if parsed:
        draft.ingestion_method = (
            IngestionMethod.UPLOADED_PAGE if any(s.from_upload for s in texts) else IngestionMethod.MANUAL_PASTE
        ).value
        draft.raw_extraction = _jsonable({
            "parser": "rules-v1",
            "sources": [
                {"source": p.source, "fields": p.fields, "features": p.features,
                 "history": p.history, "extras": p.extras, "notes": p.notes}
                for p in parsed
            ],
            "conflicts": draft.conflicts,
        })
    draft.duration_ms = int((time.monotonic() - started) * 1000)
    return draft


# --------------------------------------------------------------------------- review -> validated models
def missing_required(values: dict[str, str]) -> list[str]:
    return [label for key, label in REQUIRED_FIELDS.items() if not (values.get(key) or "").strip()]


def build_models(values: dict[str, str], features: dict[str, str], reference_url: str | None):
    """Raises pydantic.ValidationError if any field is invalid."""
    payload = {k: v for k, v in values.items() if k in PROPERTY_FIELDS}
    payload["listing_url"] = reference_url
    return PropertyExtract(**payload), FeaturesInput(**{k: v for k, v in features.items() if k in FEATURE_FIELDS})


def manual_overrides(initial: dict[str, str], final: dict[str, str]) -> dict[str, bool]:
    """Fields whose final text differs from what extraction produced (typed in or corrected by you)."""
    return {
        k: True
        for k in PROPERTY_FIELDS
        if (initial.get(k) or "").strip() != (final.get(k) or "").strip()
    }


def features_detail(initial: dict[str, str], final: dict[str, str], draft_sources: list[str]) -> dict:
    """Per-field provenance: unchanged extracted values come from the listing, edits are manual."""
    detail = {}
    for k in FEATURE_FIELDS:
        value = (final.get(k) or "").strip()
        if not value:
            continue
        if value == (initial.get(k) or "").strip() and value:
            detail[k] = {"value": value, "source_type": "listing_field", "source": ", ".join(draft_sources)}
        else:
            detail[k] = {"value": value, "source_type": "manual"}
    return detail


# --------------------------------------------------------------------------- logging & saving
def _source_id(session: Session, name: str | None):
    if not name:
        return None
    return session.scalar(select(DataSource.id).where(DataSource.name == name))


def log_extraction(session: Session, draft: Draft) -> str | None:
    """Record the extraction attempt (no raw page content). Returns the log id."""
    if not (draft.sources or draft.unrecognized or draft.rejected_reason):
        return None
    if draft.rejected_reason:
        status, message = LogStatus.REJECTED, draft.rejected_reason
    elif not draft.sources:
        status, message = LogStatus.FAILED, "Source text was not recognized."
    elif all(draft.values.get(k) for k in ("address", "city", "asking_price")):
        status, message = LogStatus.SUCCESS, None
    else:
        status, message = LogStatus.PARTIAL, "Some key fields were not found."
    row = ExtractionLog(
        source_id=_source_id(session, draft.sources[0] if draft.sources else None),
        ingestion_method=draft.ingestion_method,
        reference_url=draft.reference_url,
        status=status.value,
        message=message,
        fields_extracted=draft.fields_extracted or None,
        fields_missing=draft.fields_missing or None,
        duration_ms=draft.duration_ms,
    )
    session.add(row)
    session.flush()
    return str(row.id)


def _link_log(session: Session, log_id: str | None, prop: Property) -> None:
    if not log_id:
        return
    log = session.get(ExtractionLog, uuid.UUID(str(log_id)))
    if log is not None:
        log.property_id = prop.id


def selected_history(draft: Draft, import_history: bool) -> list[dict] | None:
    return draft.history_rows if (import_history and draft.history_rows) else None


def save_new(
    session: Session, draft: Draft, data: PropertyExtract, feats: FeaturesInput,
    initial_values: dict[str, str], final_values: dict[str, str],
    initial_features: dict[str, str], final_features: dict[str, str], *, import_history: bool,
) -> Property:
    prop = create_property(
        session, data,
        ingestion_method=IngestionMethod(draft.ingestion_method),
        raw_extraction=draft.raw_extraction,
        features=feats,
        features_detail=features_detail(initial_features, final_features, draft.sources),
        manual_overrides=manual_overrides(initial_values, final_values),
        history_rows=selected_history(draft, import_history),
        verified=True,  # you reviewed every field on the review screen before saving
    )
    _link_log(session, draft.log_id, prop)
    return prop


def update_existing(
    session: Session, prop: Property, draft: Draft, data: PropertyExtract, feats: FeaturesInput,
    initial_features: dict[str, str], final_features: dict[str, str], *, import_history: bool,
) -> Property:
    update_property_from_new_listing(
        session, prop, data, note="Updated from new listing data (reviewed)",
        history_rows=selected_history(draft, import_history),
    )
    fill_missing_features(session, prop, feats, features_detail(initial_features, final_features, draft.sources))
    _link_log(session, draft.log_id, prop)
    return prop


def duplicates_for(session: Session, data: PropertyExtract):
    return find_duplicates(
        session, listing_number=data.listing_number, address=data.address, unit=data.unit,
        postal_code=data.postal_code, listing_url=data.listing_url,
    )
