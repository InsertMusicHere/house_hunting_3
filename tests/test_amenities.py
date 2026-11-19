from services.research.amenities import build_query, parse_elements, summarize, haversine_m


def test_query_contains_radius_and_categories():
    q = build_query(43.65, -79.38, 1500)
    assert "around:1500" in q and '"pharmacy"' in q and "out center" in q


def test_parse_deduplicates_and_ignores_outside_radius():
    payload = {"elements": [
        {"type": "node", "id": 1, "lat": 43.65, "lon": -79.38, "tags": {"shop": "supermarket"}},
        {"type": "way", "id": 2, "center": {"lat": 43.6501, "lon": -79.38}, "tags": {"leisure": "park"}},
        {"type": "node", "id": 3, "lat": 44.0, "lon": -79.38, "tags": {"amenity": "pharmacy"}},
    ]}
    items = parse_elements(payload, 43.65, -79.38, 1500)
    assert len(items) == 2
    assert {x.category for x in items} == {"grocery", "park"}
    result = summarize(items)
    assert result["covered_categories"] == 2
    assert result["score"] == 28.6


def test_distance_is_straight_line():
    assert haversine_m(43.65, -79.38, 43.65, -79.38) == 0


def test_invalid_radius():
    import pytest
    with pytest.raises(ValueError):
        build_query(43.65, -79.38, 10000)
