from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from database.enums import IngestionMethod
from database.models import ExtractionLog, ListingHistory, Property
from services import ingestion as svc

FIX = Path(__file__).parent / "fixtures"
HS = (FIX / "housesigma_synthetic.txt").read_text(encoding="utf-8")
RT = (FIX / "realtor_synthetic.txt").read_text(encoding="utf-8")


def draft_both():
    return svc.extract_draft([svc.SourceInput(HS), svc.SourceInput(RT)], "https://www.realtor.ca/real-estate/1/x/")


def test_merge_prefers_realtor_and_reports_conflicts():
    d = draft_both()
    assert d.values["maintenance_fee"] == "499.75"            # Realtor.ca is more precise
    assert d.conflicts["maintenance_fee"][0][0] == "Realtor.ca"
    assert {s for s, _ in d.conflicts["maintenance_fee"]} == {"Realtor.ca", "HouseSigma"}
    assert d.values["listed_tax_year"] == "2025"               # only HouseSigma has it
    assert d.values["listing_date"] == "2026-11-13"
    assert d.values["description"].startswith("Invented description")  # original text, not the AI summary
    assert d.values["listing_status"] == "For Sale"
    assert len(d.history_rows) == 4
    assert d.ingestion_method == "manual_paste"
    assert "year_built" in d.fields_missing and "address" not in d.fields_missing


def test_agreeing_sources_report_no_conflict():
    d = draft_both()
    for k in ("address", "city", "listing_number", "asking_price", "unit"):
        assert k not in d.conflicts


def test_property_features_are_merged_unique():
    d = draft_both()
    assert "Playground" in d.features["property_features"]
    assert d.features["basement"] == "Finished"
    assert "basement" not in d.conflicts


def test_upload_sets_ingestion_method():
    d = svc.extract_draft([svc.SourceInput(RT, from_upload=True, filename="x.html")])
    assert d.ingestion_method == "uploaded_page"


def test_unrecognized_text_leaves_blank_form():
    d = svc.extract_draft([svc.SourceInput("some random pasted words")])
    assert d.unrecognized == 1 and not d.sources and d.ingestion_method == "manual_entry"
    assert all(v == "" for v in d.values.values()) and d.notes


def test_apartment_rejected():
    d = svc.extract_draft([svc.SourceInput(RT.replace("Row / Townhouse", "Apartment"))])
    assert d.rejected_reason and not d.values


def test_required_fields_and_validation_messages():
    assert svc.missing_required({"address": "", "city": "Toronto"}) == ["Street address"]
    d = draft_both()
    vals = dict(d.values, postal_code="12345")
    with pytest.raises(ValidationError):
        svc.build_models(vals, d.features, None)


def test_manual_overrides_only_changed_fields():
    d = draft_both()
    final = dict(d.values, asking_price="480000", direction="North")
    assert svc.manual_overrides(d.values, final) == {"asking_price": True, "direction": True}


def test_features_detail_marks_source():
    d = draft_both()
    final = dict(d.features, sunlight_rating="Great")
    detail = svc.features_detail(d.features, final, d.sources)
    assert detail["flooring"]["source_type"] == "listing_field"
    assert detail["sunlight_rating"]["source_type"] == "manual"


def _save(session, draft, **over):
    final = dict(draft.values, **over)
    data, feats = svc.build_models(final, draft.features, draft.reference_url)
    return svc.save_new(session, draft, data, feats, draft.values, final, draft.features, draft.features,
                        import_history=True)


def test_save_new_end_to_end(session):
    from database.seed import seed_data_sources
    seed_data_sources(session)
    d = draft_both()
    d.log_id = svc.log_extraction(session, d)
    prop = _save(session, d, direction="North", expected_offer_price="490000")
    session.commit()
    assert prop.source.name == "Realtor.ca"
    assert prop.extraction_status == "verified" and prop.last_verified is not None
    assert prop.direction == "North" and str(prop.expected_offer_price) == "490000.00"
    assert prop.manual_overrides == {"direction": True, "expected_offer_price": True}
    assert prop.features.flooring == "Laminate, Ceramic, Carpeted"
    assert prop.features.ai_detail["flooring"]["source_type"] == "listing_field"
    assert len(prop.history) == 5  # 4 imported + "Initial entry"
    assert {h.listing_status for h in prop.history} == {"For Sale", "Terminated"}
    assert prop.raw_extraction["sources"][0]["source"] == "Realtor.ca"
    assert "<html" not in str(prop.raw_extraction).lower()
    log = session.scalars(select(ExtractionLog)).one()
    assert log.property_id == prop.id and log.status == "success"


def test_update_existing_keeps_overrides_and_does_not_duplicate_history(session):
    d = draft_both()
    prop = _save(session, d, bedrooms="4")
    prop.manual_overrides = {"bedrooms": True}
    session.commit()
    d2 = draft_both()
    final2 = dict(d2.values, asking_price="480000")
    data2, feats2 = svc.build_models(final2, d2.features, d2.reference_url)
    assert [m.reasons for m in svc.duplicates_for(session, data2)][0] == ("listing_number", "address", "url")
    svc.update_existing(session, prop, d2, data2, feats2, d2.features, d2.features, import_history=True)
    session.commit()
    assert str(prop.asking_price) == "480000.00" and prop.bedrooms == 4
    assert len(prop.history) == 6  # 5 before + 1 update row; imported rows were not repeated
    assert session.query(Property).count() == 1


def test_log_rejected_and_failed(session):
    d = svc.extract_draft([svc.SourceInput(RT.replace("Row / Townhouse", "Apartment"))])
    svc.log_extraction(session, d)
    d2 = svc.extract_draft([svc.SourceInput("nonsense")])
    svc.log_extraction(session, d2)
    session.commit()
    assert sorted(l.status for l in session.scalars(select(ExtractionLog))) == ["failed", "rejected"]
    assert svc.log_extraction(session, svc.extract_draft([])) is None  # manual entry logs nothing


def test_two_saved_pages_merge_end_to_end():
    from parsers.html_to_text import html_to_text
    rt = html_to_text((FIX / "realtor_saved_synthetic.html").read_bytes())
    hs = html_to_text((FIX / "housesigma_saved_synthetic.html").read_bytes())
    d = svc.extract_draft([svc.SourceInput(rt, from_upload=True), svc.SourceInput(hs, from_upload=True)])
    assert d.ingestion_method == "uploaded_page"
    assert d.values["address"] == "123 Example Road W" and d.values["asking_price"] == "500000"
    assert d.values["listing_status"] == "For Sale" and len(d.history_rows) == 3
    assert d.values["description"].startswith("Invented description")  # Realtor.ca wins the merge
    assert svc.missing_required(d.values) == []
