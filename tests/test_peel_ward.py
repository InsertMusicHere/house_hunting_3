import pytest
from services.research.peel_ward import parse_ward_csv, evaluate_ward, WardDataError

def sample(n=11):
    head = 'municipality,ward,year,population,source_url,assault,auto_theft,break_enter,robbery,theft_over\n'
    return (head + ''.join(f'mississauga,{i},2025,100000,https://example.org/data,{i},0,0,0,0\n' for i in range(1,n+1))).encode()

def test_valid_counts_and_index():
    rows = parse_ward_csv(sample())
    result = evaluate_ward(rows, 'mississauga', 1, 2025)
    assert result['relative_indicator_score'] is not None
    assert result['counts']['assault'] == 1

def test_missing_ward():
    with pytest.raises(WardDataError):
        evaluate_ward(parse_ward_csv(sample()), 'mississauga', 12, 2025)

def test_too_few_peers_no_index():
    assert evaluate_ward(parse_ward_csv(sample(3)), 'mississauga', 1, 2025)['relative_indicator_score'] is None

def test_negative_counts_rejected():
    with pytest.raises(WardDataError):
        parse_ward_csv(sample().replace(b',1,0,0,0,0', b',-1,0,0,0,0'))
