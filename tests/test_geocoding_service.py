from services.geocoding_service import parse_candidates, valid_coordinates
from services.research.cache import key_for


def test_parse_rejects_invalid_and_outside_ontario():
    rows = [{'lat': '43.65', 'lon': '-79.38', 'display_name': 'Toronto', 'type': 'city'},
            {'lat': '40', 'lon': '-73', 'display_name': 'Elsewhere'}]
    result = parse_candidates(rows)
    assert len(result) == 1
    assert result[0].confidence == 'low'


def test_coordinates():
    assert valid_coordinates(43.65, -79.38)
    assert not valid_coordinates(100, 0)


def test_cache_key_stable():
    assert key_for('x', {'a': 1, 'b': 2}) == key_for('x', {'b': 2, 'a': 1})
