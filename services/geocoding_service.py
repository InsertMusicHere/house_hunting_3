"""Explicit, single-address Nominatim lookups. Never automatically accept a result."""
from __future__ import annotations
from dataclasses import dataclass
import re
import httpx

URL = 'https://nominatim.openstreetmap.org/search'
SOURCE = 'https://www.openstreetmap.org/copyright'

@dataclass(frozen=True)
class Candidate:
    latitude: float
    longitude: float
    display_name: str
    precision: str
    confidence: str
    source_url: str


def parse_candidates(rows: list[dict]) -> list[Candidate]:
    out = []
    for item in rows[:5]:
        try:
            lat, lon = float(item['lat']), float(item['lon'])
            if not (41 <= lat <= 57 and -96 <= lon <= -74):
                continue  # rough Ontario bounds, not a legal province polygon
            kind = item.get('type', '')
            precision = 'building' if kind in ('house', 'building') else 'approximate'
            out.append(Candidate(lat, lon, str(item['display_name']), precision,
                                 'medium' if precision == 'building' else 'low', SOURCE))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def search_address(address: str, city: str, postal_code: str | None, *, user_agent: str,
                   client: httpx.Client | None = None) -> list[Candidate]:
    if not address.strip() or not city.strip():
        raise ValueError('Address and city are required')
    if not user_agent or 'example' in user_agent.lower():
        raise ValueError('Configure an identifying Nominatim User-Agent')
    query = ', '.join(filter(None, [address.strip(), city.strip(), 'Ontario', postal_code, 'Canada']))
    def run(c: httpx.Client):
        response = c.get(URL, params={'q': query, 'format': 'jsonv2', 'limit': 5,
                                      'countrycodes': 'ca', 'addressdetails': 1},
                         headers={'User-Agent': user_agent, 'Accept': 'application/json'}, timeout=15)
        response.raise_for_status()
        return parse_candidates(response.json())
    if client is not None:
        return run(client)
    with httpx.Client() as owned:
        return run(owned)


def valid_coordinates(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180
