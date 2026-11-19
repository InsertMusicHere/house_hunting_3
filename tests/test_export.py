"""Excel and CSV export: contents, filtering/sort order, formula safety, history sheet."""
import io
from datetime import datetime, timezone

import pandas as pd
import pytest
from openpyxl import load_workbook

from database import repository as repo
from services import export_service as exp
from services import property_master as pm
from tests.master_helpers import add


@pytest.fixture()
def built(session):
    add(session, address="12 Maple Street", listing_number="C1", asking_price="$900,000", maintenance_fee="450.50",
        interior_size_min=1000, interior_size_max=1000, description="Bright home.", features={"flooring": "Oak"})
    add(session, address="99 Oak Avenue", listing_number="C2", postal_code="L5G 1A1", asking_price=None,
        description="=HYPERLINK(\"http://example.invalid\",\"click\")")
    props = repo.list_properties(session)
    df = pm.build_frame([pm.property_row(p) for p in props])
    return session, props, pm.sort_frame(df, "address", True)


def sheet(data, name="Properties"):
    return load_workbook(io.BytesIO(data))[name]


def test_xlsx_has_chosen_columns_in_order_with_labels(built):
    _, _, df = built
    ws = sheet(exp.build_xlsx(df, ["address", "asking_price", "flooring"]))
    assert [c.value for c in ws[1]] == ["Address", "Asking price", "Flooring"]
    assert ws.max_row == 3 and ws.freeze_panes == "A2"
    assert ws["A2"].value == "12 Maple Street" and ws["A3"].value == "99 Oak Avenue"   # keeps the sort order
    assert ws["B2"].value == 900000 and ws["B2"].number_format == '"$"#,##0.00'
    assert ws["B3"].value is None and ws["C3"].value is None                           # blank stays blank
    assert ws["A1"].font.name == "Arial" and ws["A1"].font.bold


def test_xlsx_number_and_date_types(built):
    _, _, df = built
    ws = sheet(exp.build_xlsx(df, ["maintenance_fee", "interior_size_min", "data_quality", "date_added"]))
    assert ws["A2"].value == 450.5 and ws["B2"].value == 1000 and isinstance(ws["B2"].value, int)
    assert ws["C2"].value == 87 and isinstance(ws["D2"].value, datetime)
    assert ws["D2"].number_format == "yyyy-mm-dd hh:mm"


def test_description_only_when_requested(built):
    _, _, df = built
    without = sheet(exp.build_xlsx(df, ["address"]))
    assert [c.value for c in without[1]] == ["Address"]
    with_desc = sheet(exp.build_xlsx(df, ["address", "description"]))
    assert with_desc["B1"].value == "Description" and with_desc["B2"].value == "Bright home."


def test_formula_like_text_is_stored_as_text_not_a_formula(built):
    _, _, df = built
    ws = sheet(exp.build_xlsx(df, ["address", "description"]))
    cell = ws["B3"]
    assert cell.value.startswith("=HYPERLINK") and cell.data_type == "s"
    import zipfile
    xml = zipfile.ZipFile(io.BytesIO(exp.build_xlsx(df, ["address", "description"]))).read("xl/worksheets/sheet1.xml")
    assert b"<f>" not in xml                                 # no formula element anywhere in the sheet
    csv = exp.build_csv(df, ["address", "description"]).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv


def test_filtered_rows_only(built):
    _, _, df = built
    only = pm.apply_filters(df, pm.Filters(text="maple"))
    ws = sheet(exp.build_xlsx(only, ["address"]))
    assert ws.max_row == 2 and ws["A2"].value == "12 Maple Street"


def test_history_sheet_is_optional_and_lists_rows(built):
    session, props, df = built
    labels = {str(p.id): (p.address, p.listing_number) for p in props}
    hist = exp.history_frame(repo.history_for_properties(session, [p.id for p in props]), labels)
    assert "Listing history" not in load_workbook(io.BytesIO(exp.build_xlsx(df, ["address"]))).sheetnames
    wb = load_workbook(io.BytesIO(exp.build_xlsx(df, ["address"], hist)))
    ws = wb["Listing history"]
    assert [c.value for c in ws[1]] == exp.HISTORY_HEADERS
    assert ws.max_row == 3                                  # one 'Initial entry' row per property
    rows = {ws.cell(r, 1).value: ws.cell(r, 4).value for r in (2, 3)}
    assert rows == {"12 Maple Street": 900000, "99 Oak Avenue": None}
    assert ws["F2"].value == "Initial entry"


def test_history_for_a_deleted_property_is_labelled():
    class H:
        property_id = "gone"; observed_at = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        asking_price = None; listing_status = None; change_note = "x"; listing_url = None
    assert exp.history_frame([H()], {}).iloc[0]["Property"] == "(deleted)"


def test_csv_content_bom_and_labels(built):
    _, _, df = built
    raw = exp.build_csv(df, ["address", "asking_price", "date_added"])
    assert raw.startswith(b"\xef\xbb\xbf")
    back = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
    assert list(back.columns) == ["Address", "Asking price", "Date added"]
    assert list(back["Address"]) == ["12 Maple Street", "99 Oak Avenue"]
    assert back["Asking price"].iloc[0] == 900000 and pd.isna(back["Asking price"].iloc[1])


def test_empty_selection_still_builds_a_valid_header_only_file(built):
    _, _, df = built
    empty = pm.apply_filters(df, pm.Filters(text="nothing-matches"))
    ws = sheet(exp.build_xlsx(empty, ["address", "city"]))
    assert ws.max_row == 1 and ws["A1"].value == "Address"