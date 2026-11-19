import pytest
from services.research.peel_stats import parse_full_year_counts, PeelDataUnavailable

HTML = '''<table><tr><th>Crime</th><th>2025 Full Year</th><th>2024 Full Year</th></tr>
<tr><td>Robberies</td><td>830</td><td>997</td></tr></table>
<table><tr><th>Crime</th><th>2025 Full Year</th></tr>
<tr><td>Break and Enters</td><td>2,815</td></tr></table>
<table><tr><th>Crime</th><th>2025 Full Year</th></tr>
<tr><td>Theft of Motor Vehicles</td><td>5,280</td></tr></table>'''

def test_parse_full_year():
    result = parse_full_year_counts(HTML)
    assert result['year'] == 2025
    assert result['counts']['break_enter'] == 2815
    assert result['relative_indicator_score'] is None

def test_unavailable_year():
    with pytest.raises(PeelDataUnavailable):
        parse_full_year_counts(HTML, 2023)

def test_missing_tables():
    with pytest.raises(PeelDataUnavailable):
        parse_full_year_counts('<html>no tables</html>')
