from pathlib import Path

from database.enums import ListingStatus, PropertyType
from parsers import AUTO, detect_site, parse_text
from parsers.html_to_text import UploadTooLarge, html_to_text

FIX = Path(__file__).parent / "fixtures"
HS = (FIX / "housesigma_synthetic.txt").read_text(encoding="utf-8")
RT = (FIX / "realtor_synthetic.txt").read_text(encoding="utf-8")


def test_detects_sites():
    assert detect_site(HS) == "HouseSigma"
    assert detect_site(RT) == "Realtor.ca"
    assert detect_site("hello world") is None
    assert parse_text("hello world") is None


def test_realtor_facts():
    p = parse_text(RT)
    f = p.fields
    assert (f["unit"], f["address"], f["city"], f["neighbourhood"]) == ("7", "123 Example Road W", "Brampton", "Springdale")
    assert f["postal_code"] == "L6X 1A1"
    assert f["listing_number"] == "W1000001"
    assert str(f["asking_price"]) == "500000"
    assert (f["bedrooms"], f["bathrooms"]) == (3, 3)
    assert (f["interior_size_min"], f["interior_size_max"]) == (1400, 1599)
    assert str(f["maintenance_fee"]) == "499.75"
    assert str(f["listed_annual_property_tax"]) == "2899.50"
    assert f["parking_spaces"] == 3
    assert f["property_type"] == PropertyType.CONDO_TOWNHOUSE
    assert any("inferred" in n for n in p.notes)  # inference is disclosed, never silent
    assert f["description"].startswith("Invented description")
    assert p.features["flooring"] == "Laminate, Ceramic, Carpeted"
    assert "Outdoor pool" in p.features["property_features"]
    assert p.extras["partial_bathrooms"] == 1
    assert len(p.extras["rooms"]) == 8
    assert p.extras["rooms"][0] == {"level": "Main Level", "room": "Living room", "dimensions": "15 ft ,4 in x 13 ft"}


def test_realtor_never_invents_missing_values():
    p = parse_text(RT)
    assert "listing_status" not in p.fields      # page does not state it
    assert "listing_date" not in p.fields        # only "1 day ago"; never converted
    assert "year_built" not in p.fields


def test_housesigma_facts_and_history():
    p = parse_text(HS)
    f = p.fields
    assert (f["unit"], f["address"], f["city"], f["neighbourhood"]) == ("7", "123 Example Road W", "Brampton", "Springdale")
    assert f["listing_status"] == ListingStatus.FOR_SALE
    assert str(f["listed_annual_property_tax"]) == "2900" and f["listed_tax_year"] == 2025
    assert str(f["maintenance_fee"]) == "500"
    assert str(f["listing_date"]) == "2026-11-13"
    assert f["parking_spaces"] == 3 and f["locker"] == "None"
    assert len(p.history) == 4
    latest = max(p.history, key=lambda r: r["observed_on"])
    assert latest["event"] == "For Sale" and latest["ended_on"] is None
    older = [r for r in p.history if r["listing_status"] == ListingStatus.TERMINATED]
    assert len(older) == 3
    assert any("summary" in n.lower() for n in p.notes)  # AI-summary warning
    assert "Nothing here comes from a real listing." in f["description"]
    assert "School" not in f["description"]  # stops before nearby schools


def test_apartment_is_out_of_scope():
    p = parse_text(HS.replace("Condo Townhouse, 2-Storey", "Condo Apt, 1-Storey").replace("\nCondo Townhouse\n", "\nCondo Apt\n"))
    assert p.out_of_scope
    q = parse_text(RT.replace("Row / Townhouse", "Apartment"))
    assert q.out_of_scope


def test_realtor_building_type_house_is_not_guessed():
    p = parse_text(RT.replace("Row / Townhouse", "House").replace("Condominium/Strata", "Freehold"))
    assert "property_type" not in p.fields
    assert any("choose one" in n for n in p.notes)


def test_realtor_freehold_townhouse_and_semi():
    p = parse_text(RT.replace("Condominium/Strata", "Freehold"))
    assert p.fields["property_type"] == PropertyType.FREEHOLD_TOWNHOUSE
    q = parse_text(RT.replace("Row / Townhouse", "Semi-detached"))
    assert q.fields["property_type"] == PropertyType.SEMI_DETACHED


def test_house_address_without_unit():
    p = parse_text(RT.replace("7 - 123 EXAMPLE ROAD W", "123 EXAMPLE ROAD W"))
    assert "unit" not in p.fields and p.fields["address"] == "123 Example Road W"


def test_html_upload_uses_same_parsers():
    html = "<html><head><title>x</title><script>var a='$999';</script></head><body>" + "".join(
        f"<div>{line}</div>" for line in RT.splitlines()) + "<style>.a{}</style></body></html>"
    text = html_to_text(html.encode())
    assert "$999" not in text and ".a{}" not in text
    p = parse_text(text)
    assert p.fields["listing_number"] == "W1000001" and p.fields["postal_code"] == "L6X 1A1"
    assert p.fields["description"].startswith("Invented description")


def test_html_upload_size_limit():
    import pytest
    with pytest.raises(UploadTooLarge):
        html_to_text(b"x" * (10 * 1024 * 1024 + 1))


def test_mls_label_survives_encoding_damage():
    """A file read with the wrong encoding turns the (R) sign into 'Â®'; parsing must still work."""
    damaged = RT.replace("MLS®", "MLSÂ®")
    assert parse_text(damaged).fields["listing_number"] == "W1000001"
    assert detect_site(damaged) == "Realtor.ca"


def test_html_declared_charset_is_respected():
    html = ('<html><head><meta charset="windows-1252"></head><body><div>MLS® Number: W1000001</div></body></html>'
            ).encode("cp1252")
    assert "MLS® Number: W1000001" in html_to_text(html)


# ---- saved-page layouts (one element per line; recorded from real saved pages, content invented) ----
RT_SAVED = (FIX / "realtor_saved_synthetic.html").read_bytes()
HS_SAVED = (FIX / "housesigma_saved_synthetic.html").read_bytes()


def test_realtor_saved_page_layout():
    """Header sits behind navigation text, (R) is its own element, metric AND imperial room sizes, 'similar listings' below."""
    text = html_to_text(RT_SAVED)
    assert "999,999" not in text and "W9999999" not in text  # script decoys dropped
    assert detect_site(text) == "Realtor.ca"
    f = parse_text(text).fields
    assert f["listing_number"] == "W1000001"                      # not the similar listing's W2000002
    assert (f["unit"], f["address"], f["city"], f["neighbourhood"], f["postal_code"]) == (
        "7", "123 Example Road W", "Brampton", "Springdale", "L6X 1A1")
    assert str(f["asking_price"]) == "500000"                     # not the similar listing's $450,000
    assert (f["bedrooms"], f["bathrooms"]) == (3, 3)
    assert (f["interior_size_min"], f["interior_size_max"]) == (1400, 1599)
    assert str(f["maintenance_fee"]) == "499.75" and f["parking_spaces"] == 3
    p = parse_text(text)
    rooms = p.extras["rooms"]
    assert [r["room"] for r in rooms] == ["Living room", "Kitchen", "Recreational, Games room", "Primary Bedroom"]
    assert rooms[0]["dimensions"] == "15 ft ,4 in x 13 ft" and rooms[0]["dimensions_metric"] == "4.68 m x 3.98 m"
    assert rooms[2]["level"] == "Lower Level"


def test_housesigma_saved_page_layout():
    """City / '/' / neighbourhood on three lines, price on the next line, history one cell per line, original text first."""
    text = html_to_text(HS_SAVED)
    p = parse_text(text)
    f = p.fields
    assert (f["city"], f["neighbourhood"]) == ("Brampton", "Springdale")
    assert str(f["asking_price"]) == "500000"
    assert (f["bedrooms"], f["bathrooms"]) == (3, 3)              # not the similar listing's 2 bathrooms
    assert (f["unit"], f["address"]) == ("7", "123 Example Road W")
    assert f["listing_status"] == ListingStatus.FOR_SALE
    assert [(str(r["observed_on"]), str(r["asking_price"]), r["event"], r["listing_id"]) for r in p.history] == [
        ("2026-11-13", "500000", "For Sale", "W1000001"),
        ("2026-10-22", "510000", "Terminated", "W1000002"),
        ("2026-09-16", "525000", "Expired", "W1000003"),
    ]
    assert p.history[0]["ended_on"] is None and str(p.history[1]["ended_on"]) == "2026-11-13"
    assert f["description"] == "Invented original listing text for tests. Nothing here comes from a real listing."
    assert not any("summary" in n.lower() for n in p.notes)      # original text confirmed, no warning


def test_saved_pages_match_the_pasted_equivalents():
    rt_saved = parse_text(html_to_text(RT_SAVED)).fields
    rt_paste = parse_text(RT).fields
    for k in ("unit", "address", "city", "neighbourhood", "postal_code", "listing_number", "asking_price",
              "bedrooms", "bathrooms", "interior_size_min", "interior_size_max", "maintenance_fee",
              "listed_annual_property_tax", "parking_spaces", "property_type"):
        assert rt_saved[k] == rt_paste[k], k
