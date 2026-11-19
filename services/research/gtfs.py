"""Offline GTFS static-schedule analysis of an explicitly uploaded feed ZIP.

No download, real-time claims, route planning or API calls. Times are local to feed.
"""
from __future__ import annotations
import csv
from datetime import date, timedelta
from io import StringIO, TextIOWrapper, BytesIO
from zipfile import ZipFile, BadZipFile
from services.research.amenities import haversine_m

MAX_ZIP_BYTES = 30_000_000
MAX_UNCOMPRESSED = 180_000_000
MAX_STOPS = 150_000
MAX_ROWS = 2_000_000


def _csv(zf, filename):
    match = next((n for n in zf.namelist() if n.lower().split('/')[-1] == filename), None)
    if not match: return []
    with zf.open(match) as raw:
        return list(csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', newline='')))


def _seconds(value):
    try:
        h, m, s = (int(x) for x in value.split(':'))
        if not (0 <= h <= 47 and 0 <= m < 60 and 0 <= s < 60): return None
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError, TypeError): return None


def _active_services(zf, on_date: date):
    stamp = on_date.strftime('%Y%m%d')
    weekday = on_date.strftime('%A').lower()
    active = set()
    for row in _csv(zf, 'calendar.txt'):
        if row.get('start_date', '') <= stamp <= row.get('end_date', '') and row.get(weekday) == '1':
            active.add(row['service_id'])
    for row in _csv(zf, 'calendar_dates.txt'):
        if row.get('date') == stamp:
            if row.get('exception_type') == '1': active.add(row['service_id'])
            if row.get('exception_type') == '2': active.discard(row['service_id'])
    return active


def analyze_zip(data: bytes, lat: float, lon: float, on_date: date, *, radius_m: int = 800, start_hour: int = 7, end_hour: int = 9):
    if len(data) > MAX_ZIP_BYTES: raise ValueError('GTFS ZIP exceeds 30 MB limit')
    if not (100 <= radius_m <= 3000 and 0 <= start_hour < end_hour <= 24): raise ValueError('Invalid radius or hour window')
    try:
        zf = ZipFile(BytesIO(data))
    except BadZipFile as exc: raise ValueError('Not a valid GTFS ZIP') from exc
    with zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_UNCOMPRESSED: raise ValueError('GTFS uncompressed size exceeds limit')
        names = {n.split('/')[-1].lower() for n in zf.namelist()}
        if not {'stops.txt', 'trips.txt', 'stop_times.txt'}.issubset(names): raise ValueError('GTFS ZIP missing required files')
        if any(i.file_size > MAX_UNCOMPRESSED for i in zf.infolist()): raise ValueError('Oversized GTFS file')
        stops = _csv(zf, 'stops.txt')
        if len(stops) > MAX_STOPS: raise ValueError('Too many stops')
        nearby = {}
        for row in stops:
            try:
                dist = haversine_m(lat, lon, float(row['stop_lat']), float(row['stop_lon']))
            except (ValueError, KeyError): continue
            if dist <= radius_m:
                nearby[row['stop_id']] = {'stop_id': row['stop_id'], 'stop_name': row.get('stop_name', ''), 'distance_m': round(dist), 'departures': 0}
        services = _active_services(zf, on_date)
        trips = _csv(zf, 'trips.txt')
        if len(trips) > MAX_ROWS: raise ValueError('Too many trips')
        active_trips = {r['trip_id'] for r in trips if r.get('service_id') in services}
        # Limit memory by streaming stop_times rather than loading the entire file.
        match = next(n for n in zf.namelist() if n.lower().split('/')[-1] == 'stop_times.txt')
        count = 0
        with zf.open(match) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
            for row in reader:
                count += 1
                if count > MAX_ROWS: raise ValueError('Too many stop times')
                sid = row.get('stop_id')
                if sid not in nearby or row.get('trip_id') not in active_trips: continue
                sec = _seconds(row.get('departure_time', ''))
                if sec is not None and start_hour * 3600 <= sec < end_hour * 3600:
                    nearby[sid]['departures'] += 1
        # GTFS frequencies.txt may define additional frequency-based trips; do not claim completeness.
        frequency_present = 'frequencies.txt' in names
        agency = _csv(zf, 'agency.txt')
        feed = _csv(zf, 'feed_info.txt')
        return {'date': on_date.isoformat(), 'start_hour': start_hour, 'end_hour': end_hour,
                'radius_m': radius_m, 'agency': agency[0].get('agency_name', 'Unknown agency') if agency else 'Unknown agency',
                'feed_version': feed[0].get('feed_version') if feed else None,
                'feed_start_date': feed[0].get('feed_start_date') if feed else None,
                'feed_end_date': feed[0].get('feed_end_date') if feed else None,
                'frequency_based_service_not_counted': frequency_present,
                'stops': sorted(nearby.values(), key=lambda x: (-x['departures'], x['distance_m'])),
                'total_stop_departures': sum(s['departures'] for s in nearby.values()),
                'note': 'Scheduled stop departures, not unique vehicles, real-time arrivals or journey frequency. Service exceptions applied. Frequency-based trips are not counted.'}
