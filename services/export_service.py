"""Excel and CSV export of the Property Master table. No page code and no database access.

The caller passes the already filtered and sorted DataFrame and the column keys to export.
Text that starts with = + - or @ is written as plain text, never as a formula, because
listing text is not trusted.
"""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from typing import Any, Iterable

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from database.models import ListingHistory
from services.property_master import BY_KEY, is_blank, to_local

_FONT = "Arial"
_FORMULA_STARTS = ("=", "+", "-", "@")
_NUMBER_FORMAT = {"money": '"$"#,##0.00', "int": "0", "pct": '0"%"', "datetime": "yyyy-mm-dd hh:mm", "date": "yyyy-mm-dd"}

HISTORY_HEADERS = ["Property", "MLS number", "Date observed", "Asking price", "Listing status", "Note", "Listing link"]


def _py(value: Any, kind: str) -> Any:
    """Convert a pandas cell to a plain Python value for the workbook."""
    if is_blank(value):
        return None
    if kind == "datetime":
        return pd.Timestamp(value).to_pydatetime()
    if kind in ("int", "pct"):
        return int(value)
    if kind == "money":
        return float(value)
    return str(value)


def _write_cell(ws, row: int, col: int, value: Any, kind: str):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = Font(name=_FONT, size=10)
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        cell.data_type = "s"  # keep as text; never let Excel evaluate it
    if kind in _NUMBER_FORMAT and value is not None:
        cell.number_format = _NUMBER_FORMAT[kind]
    return cell


def _style_sheet(ws, headers: list[str], widths: list[int]) -> None:
    fill = PatternFill("solid", start_color="1F3864")
    for i, name in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=_FONT, size=10, bold=True, color="FFFFFF")
        c.fill = fill
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = min(max(widths[i - 1], 10), 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _width(header: str, values: Iterable[Any]) -> int:
    longest = max([len(header)] + [len(str(v)) for v in values if v is not None] or [10])
    return longest + 2


def build_xlsx(df: pd.DataFrame, columns: list[str], history: pd.DataFrame | None = None) -> bytes:
    """One 'Properties' sheet with the chosen columns, plus an optional 'Listing history' sheet."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Properties"
    headers = [BY_KEY[k].label for k in columns]
    kinds = [BY_KEY[k].kind for k in columns]
    cells = [[_py(v, kinds[j]) for j, v in enumerate(row)] for row in df[columns].itertuples(index=False, name=None)]
    _style_sheet(ws, headers, [_width(h, (r[j] for r in cells)) for j, h in enumerate(headers)])
    for i, row in enumerate(cells, start=2):
        for j, v in enumerate(row, start=1):
            _write_cell(ws, i, j, v, kinds[j - 1])

    if history is not None:
        hs = wb.create_sheet("Listing history")
        kinds_h = ["text", "text", "date", "money", "text", "text", "text"]
        data = [list(r) for r in history[HISTORY_HEADERS].itertuples(index=False, name=None)]
        data = [[None if is_blank(v) else v for v in r] for r in data]
        _style_sheet(hs, HISTORY_HEADERS, [_width(h, (r[j] for r in data)) for j, h in enumerate(HISTORY_HEADERS)])
        for i, row in enumerate(data, start=2):
            for j, v in enumerate(row, start=1):
                _write_cell(hs, i, j, v, kinds_h[j - 1])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_csv(df: pd.DataFrame, columns: list[str]) -> bytes:
    """UTF-8 with a BOM so Excel shows accents correctly. Formula-looking text gets a leading '."""
    out = df[columns].copy()
    out.columns = [BY_KEY[k].label for k in columns]
    for name in out.columns:
        if out[name].dtype == object or pd.api.types.is_string_dtype(out[name]):
            out[name] = out[name].map(
                lambda v: "'" + v if isinstance(v, str) and v.startswith(_FORMULA_STARTS) else v
            )
    return out.to_csv(index=False).encode("utf-8-sig")


def history_frame(rows: list[ListingHistory], property_labels: dict[str, tuple[str, str | None]]) -> pd.DataFrame:
    """Listing-history rows as a table. property_labels maps property id -> (address text, MLS number)."""
    data = []
    for h in rows:
        label, mls = property_labels.get(str(h.property_id), ("(deleted)", None))
        observed = to_local(h.observed_at)
        if isinstance(observed, datetime):
            observed = observed.date()
        data.append({
            "Property": label,
            "MLS number": mls,
            "Date observed": observed if isinstance(observed, date) else None,
            "Asking price": None if h.asking_price is None else float(h.asking_price),
            "Listing status": h.listing_status,
            "Note": h.change_note,
            "Listing link": h.listing_url,
        })
    return pd.DataFrame(data, columns=HISTORY_HEADERS)

def build_table_xlsx(df: pd.DataFrame, headers: list[str], kinds: list[str], sheet_name: str = "Sheet1") -> bytes:
    """Write any DataFrame as one styled sheet. `kinds` is text | money | int | pct | datetime | date."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    cells = [[_py(v, kinds[j]) for j, v in enumerate(row)] for row in df[headers].itertuples(index=False, name=None)]
    _style_sheet(ws, headers, [_width(h, (r[j] for r in cells)) for j, h in enumerate(headers)])
    for i, row in enumerate(cells, start=2):
        for j, v in enumerate(row, start=1):
            _write_cell(ws, i, j, v, kinds[j - 1])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_table_csv(df: pd.DataFrame, headers: list[str]) -> bytes:
    out = df[headers].copy()
    for name in out.columns:
        if out[name].dtype == object or pd.api.types.is_string_dtype(out[name]):
            out[name] = out[name].map(lambda v: "'" + v if isinstance(v, str) and v.startswith(_FORMULA_STARTS) else v)
    return out.to_csv(index=False).encode("utf-8-sig")