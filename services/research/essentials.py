"""Phase 6c: OSM discovery of healthcare, schools and transit stops.

OSM tags are leads, not verification of clinical services, catchment, quality or safety.
No HTTP request occurs until fetch() is called.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from math import isfinite
import os
import httpx
from services.research.amenities import haversine_m, AmenityUnavailable

PRIMARY = 'https://ovp.dresel.systems/api-na/interpreter'
FALLBACK = 'https://overpass.private.coffee/api/interpreter'
ATTRIBUTION = '© OpenStreetMap contributors (ODbL), via Overpass API'
CATEGORIES = ('hospital', 'clinic', 'doctors', 'urgent_care', 'school', 'kindergarten', 'college', 'bus_stop', 'tram_stop', 'station', 'subway_entrance')

@dataclass(frozen=True)
class Place:
    osm_type: str
    osm_id: int
    category: str
    name: str | None
    latitude: float
    longitude: float
    distance_m: float
    tags: dict

    def public_dict(self) -> dict:
        d = asdict(self)
        d['tags'] = {k: v for k, v in self.tags.items() if k in ('website', 'operator', 'grades', 'school:level', 'isced:level', 'healthcare', 'emergency', 'maternity', 'name', 'ref', 'network', 'railway', 'public_transport')}
        return d


def query(lat: float, lon: float, radius_m: int) -> str:
    if not (isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and 100 <= radius_m <= 3000):
        raise ValueError('Invalid coordinates or radius')
    near = f'around:{radius_m},{lat:.7f},{lon:.7f}'
    return '\n'.join(['[out:json][timeout:40];', '('] + [
        f'  nwr({near})["amenity"~"^(hospital|clinic|doctors|school|kindergarten|college|bus_station)$"];',
        f'  nwr({near})["healthcare"~"^(hospital|clinic|doctor|urgent_care)$"];',
        f'  nwr({near})["highway"="bus_stop"];',
        f'  nwr({near})["railway"~"^(station|halt|tram_stop|subway_entrance)$"];',
        f'  nwr({near})["public_transport"="station"];',
        ');', 'out center tags;'])


def classify(tags: dict) -> tuple[str, ...]:
    result = []
    a, h, r = tags.get('amenity'), tags.get('healthcare'), tags.get('railway')
    if a == 'hospital' or h == 'hospital': result.append('hospital')
    if a == 'clinic' or h == 'clinic': result.append('clinic')
    if a == 'doctors' or h == 'doctor': result.append('doctors')
    if h == 'urgent_care': result.append('urgent_care')
    if a in ('school', 'kindergarten', 'college'): result.append(a)
    if tags.get('highway') == 'bus_stop': result.append('bus_stop')
    if r == 'tram_stop': result.append('tram_stop')
    if r in ('station', 'halt') or tags.get('public_transport') == 'station' or a == 'bus_station': result.append('station')
    if r == 'subway_entrance': result.append('subway_entrance')
    return tuple(dict.fromkeys(result))


def parse(payload: dict, lat: float, lon: float, radius_m: int) -> list[Place]:
    if not isinstance(payload, dict) or payload.get('remark') or not isinstance(payload.get('elements'), list):
        raise AmenityUnavailable(f'Invalid/failed Overpass response: {str(payload.get("remark"))[:180] if isinstance(payload, dict) else "invalid JSON"}')
    found = {}
    for element in payload['elements']:
        if not isinstance(element, dict): continue
        coords = element.get('center') or element
        try:
            elat, elon = float(coords['lat']), float(coords['lon'])
            oid, typ = int(element['id']), str(element['type'])
        except (KeyError, ValueError, TypeError): continue
        if not (isfinite(elat) and isfinite(elon) and -90 <= elat <= 90 and -180 <= elon <= 180): continue
        dist = haversine_m(lat, lon, elat, elon)
        if dist > radius_m: continue
        tags = element.get('tags') or {}
        for category in classify(tags):
            found[(typ, oid, category)] = Place(typ, oid, category, tags.get('name'), elat, elon, round(dist, 1), tags)
    return sorted(found.values(), key=lambda p: (p.distance_m, p.category, p.osm_id))


def fetch(lat: float, lon: float, radius_m: int, *, client: httpx.Client | None = None, user_agent: str | None = None) -> dict:
    agent = user_agent or os.getenv('NOMINATIM_USER_AGENT', '')
    if not agent or 'example.com' in agent: raise AmenityUnavailable('Configure NOMINATIM_USER_AGENT with a real contact')
    statement = query(lat, lon, radius_m)
    owned = client is None
    if owned: client = httpx.Client(timeout=httpx.Timeout(50, connect=5, read=45))
    errors = []
    try:
        for endpoint in (PRIMARY, FALLBACK):
            try:
                resp = client.post(endpoint, data={'data': statement}, headers={'User-Agent': agent, 'Accept': 'application/json'})
                resp.raise_for_status()
                result = resp.json()
                if not isinstance(result, dict) or result.get('remark') or not isinstance(result.get('elements'), list):
                    raise AmenityUnavailable('Overpass returned an error or malformed result')
                return result
            except httpx.HTTPStatusError as exc:
                errors.append(f'{endpoint}: HTTP {exc.response.status_code}')
                if exc.response.status_code not in (500, 502, 503, 504): break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, ValueError, AmenityUnavailable) as exc:
                errors.append(f'{endpoint}: {type(exc).__name__}')
        raise AmenityUnavailable('Essential services lookup unavailable: ' + '; '.join(errors))
    finally:
        if owned: client.close()


def summary(places: list[Place]) -> dict:
    return {category: {'count': sum(p.category == category for p in places), 'nearest_m': min((p.distance_m for p in places if p.category == category), default=None)} for category in CATEGORIES}
