from services.research.essentials import classify, parse, query, summary
from services.research.gtfs import _seconds, analyze_zip
from io import BytesIO
from zipfile import ZipFile
from datetime import date
import pytest


def test_query_compact():
    q = query(43.7, -79.4, 1200)
    assert q.count('nwr(') == 5
    with pytest.raises(ValueError): query(91, -79.4, 1200)


def test_hospital_not_maternity_verification():
    assert classify({'amenity': 'hospital'}) == ('hospital',)
    places = parse({'elements': [{'type': 'node', 'id': 1, 'lat': 43.7, 'lon': -79.4, 'tags': {'amenity': 'hospital', 'name': 'Test'}}]}, 43.7, -79.4, 1200)
    assert len(places) == 1
    assert summary(places)['hospital']['nearest_m'] == 0


def test_education_and_transit():
    assert 'school' in classify({'amenity': 'school'})
    assert 'bus_stop' in classify({'highway': 'bus_stop'})
    assert _seconds('25:10:00') == 90600


def test_gtfs_calendar_and_exceptions():
    out = BytesIO()
    files = {
        'stops.txt': 'stop_id,stop_name,stop_lat,stop_lon\ns1,Near,43.7,-79.4\n',
        'trips.txt': 'route_id,service_id,trip_id\nr1,weekday,t1\n',
        'stop_times.txt': 'trip_id,arrival_time,departure_time,stop_id,stop_sequence\nt1,08:00:00,08:00:00,s1,1\n',
        'calendar.txt': 'service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nweekday,1,1,1,1,1,0,0,20260101,20261231\n',
    }
    with ZipFile(out, 'w') as z:
        for k, v in files.items(): z.writestr(k, v)
    result = analyze_zip(out.getvalue(), 43.7, -79.4, date(2026, 11, 18))
    assert result['total_stop_departures'] == 1
