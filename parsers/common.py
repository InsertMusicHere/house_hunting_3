"""Shared building blocks for the site parsers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from database.enums import ListingStatus


@dataclass
class ParsedListing:
    """What one pasted source gave us. Missing facts are simply absent (never guessed)."""

    source: str
    fields: dict[str, Any] = field(default_factory=dict)      # listing facts (PropertyExtract names)
    features: dict[str, Any] = field(default_factory=dict)    # feature facts (FeaturesInput names)
    history: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)      # kept only in raw_extraction
    notes: list[str] = field(default_factory=list)            # warnings and inferences for the review screen
    out_of_scope: str | None = None                           # set for apartment condos


def clean_lines(text: str) -> list[str]:
    """Split pasted text into trimmed, non-empty lines (tabs and odd spaces collapsed)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    out = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def find_labelled(
    lines: list[str], label: str, known_labels: set[str], *, start: int = 0, end: int | None = None
) -> str | None:
    """Value for a 'Label' / 'Label:' line.

    Handles 'Label: value' on one line and 'Label' followed by the value on the next
    line. Returns None if the label is absent, or its value line is really the next label.
    """
    pat = re.compile(rf"^{re.escape(label)}\s*(?::\s*(?P<inline>.*))?$", re.I)
    stop = len(lines) if end is None else end
    for i in range(start, stop):
        m = pat.match(lines[i])
        if not m:
            continue
        inline = (m.group("inline") or "").strip()
        if inline:
            return inline
        if i + 1 < stop:
            nxt = lines[i + 1]
            if nxt.rstrip(":").strip().lower() in known_labels or nxt.endswith(":"):
                return None
            return nxt
        return None
    return None


def index_of(lines: list[str], label: str, start: int = 0) -> int | None:
    pat = re.compile(rf"^{re.escape(label)}\s*:?$", re.I)
    for i in range(start, len(lines)):
        if pat.match(lines[i]):
            return i
    return None


EVENT_TO_STATUS = {
    "for sale": ListingStatus.FOR_SALE,
    "sold": ListingStatus.SOLD,
    "terminated": ListingStatus.TERMINATED,
    "suspended": ListingStatus.SUSPENDED,
    "expired": ListingStatus.EXPIRED,
}


def event_to_status(event: str | None) -> ListingStatus | None:
    return EVENT_TO_STATUS.get((event or "").strip().lower())


def parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def put(target: dict, key: str, value: Any) -> None:
    """Set a field only when a real value was found."""
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[key] = value
