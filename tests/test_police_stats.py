from services.research.police_stats import resolve_fields, available_years, evaluate, CATEGORIES, fetch_neighbourhood, PoliceDataUnavailable
import pytest


def make_row(n):
    row = {'AREA_NAME': 'Test', 'HOOD_ID': 1}
    for category in CATEGORIES:
        row[f'{category}_2025'] = n
        row[f'{category}_RATE_2025'] = float(n)
    return row


def test_fields_and_years():
    names = set(make_row(1))
    assert available_years(names) == [2025]
    assert len(resolve_fields(names, 2025)) == 5


def test_relative_index_midrank():
    fields = resolve_fields(set(make_row(1)), 2025)
    peers = [make_row(i) for i in range(1, 101)]
    result = evaluate(make_row(1), peers, fields, 2025)
    assert result['relative_indicator_score'] == 99.5
    assert result['total_selected_count'] == 5
    assert result['peer_count'] == 100


def test_missing_rate_does_not_invent_score():
    fields = resolve_fields(set(make_row(1)), 2025)
    target = make_row(1)
    del target['ASSAULT_RATE_2025']
    result = evaluate(target, [make_row(i) for i in range(50)], fields, 2025)
    assert result['relative_indicator_score'] is None
    assert 'ASSAULT' in result['missing_categories']


def test_invalid_geography():
    with pytest.raises(PoliceDataUnavailable):
        fetch_neighbourhood(45.4, -75.7)
