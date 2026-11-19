"""Data-quality score: the share of tracked listing fields that are filled in.

The tracked fields are the 15 in schemas.property.TRACKED_FIELDS. A field counts as
filled when it holds any non-blank value. Feature fields (flooring, basement, ...) are
not counted. The score is stored as a fraction 0.00-1.00 (data_quality_score) and shown
as a percentage.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from schemas.property import TRACKED_FIELDS


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def quality_for_property(prop: Any) -> tuple[Decimal, list[str]]:
    """Return (score 0.00-1.00, names of missing tracked fields) for a Property or any object."""
    missing = [f for f in TRACKED_FIELDS if _blank(getattr(prop, f, None))]
    filled = len(TRACKED_FIELDS) - len(missing)
    score = (Decimal(filled) / Decimal(len(TRACKED_FIELDS))).quantize(Decimal("0.01"))
    return score, missing


def percent(score: Decimal | float | None) -> int | None:
    """Whole-number percentage for display (0.73 -> 73). None stays None."""
    if score is None:
        return None
    return int((Decimal(str(score)) * 100).to_integral_value())