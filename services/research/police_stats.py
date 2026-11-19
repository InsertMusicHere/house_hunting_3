"""Toronto Police neighbourhood aggregates only; never individual incident locations.

The 0-100 indicator is a *relative reported-crime-rate index*, not a safety
probability or assessment of residents. Only same-year TPS neighbourhoods compare.
"""
from __future__ import annotations
import math
import re
import httpx

LAYER = ('https://services.arcgis.com/S9th0jAJ7bqgIRjw/ArcGIS/rest/services/'
         'Neighbourhood_Crime_Rates_Open_Data/FeatureServer/0')
SOURCE = 'https://www.tps.ca/data-maps/open-data/'
CATEGORIES = ('ASSAULT', 'AUTOTHEFT', 'BREAKENTER', 'ROBBERY', 'THEFTOVER')
# Some field names vary between versions: resolve from layer metadata.
ALIASES = {
    'ASSAULT': ('ASSAULT',), 'AUTOTHEFT': ('AUTOTHEFT', 'AUTO_THEFT'),
    'BREAKENTER': ('BREAKENTER', 'BREAK_AND_ENTER', 'BREAKENTERING'),
    'ROBBERY': ('ROBBERY',), 'THEFTOVER': ('THEFTOVER', 'THEFT_OVER'),
}

class PoliceDataUnavailable(RuntimeError):
    pass


def _get(client, path, params):
    try:
        response = client.get(path, params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get('error'):
            raise PoliceDataUnavailable(f"ArcGIS error: {data.get('error') if isinstance(data, dict) else 'invalid response'}")
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise PoliceDataUnavailable(f'Toronto Police data request failed: {exc}') from exc


def resolve_fields(names, year):
    """Find matching count and rate fields; never fabricate missing indicators."""
    available = set(names)
    result = {}
    for category, variants in ALIASES.items():
        for variant in variants:
            count, rate = f'{variant}_{year}', f'{variant}_RATE_{year}'
            if count in available and rate in available:
                result[category] = (count, rate)
                break
    return result


def available_years(names):
    years = set()
    for name in names:
        match = re.fullmatch(r'ASSAULT_RATE_(20\d\d)', name)
        if match and f"ASSAULT_{match.group(1)}" in names:
            years.add(int(match.group(1)))
    return sorted(years, reverse=True)


def _valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def evaluate(target, peers, fields, year):
    """Compute count/rate and a transparent, unweighted relative index.

    The index is 100 * (1 - empirical percentile rank of sum of selected
    per-100k rates). High = lower reported selected-indicator rate relative to
    other TPS neighbourhoods. It is not a validated safety measure.
    """
    def row(attrs):
        counts, rates = {}, {}
        for category, (count_field, rate_field) in fields.items():
            count, rate = attrs.get(count_field), attrs.get(rate_field)
            if _valid_number(count) and _valid_number(rate):
                counts[category] = int(count)
                rates[category] = float(rate)
        return counts, rates

    counts, rates = row(target)
    complete = len(rates) == len(fields) and len(fields) == len(CATEGORIES)
    total_rate = sum(rates.values()) if complete else None
    score, peer_count = None, 0
    if complete:
        totals = []
        for peer in peers:
            _, peer_rates = row(peer)
            if len(peer_rates) == len(CATEGORIES):
                totals.append(sum(peer_rates.values()))
        peer_count = len(totals)
        if peer_count >= 20 and total_rate is not None:
            # Equal values share the same midrank.
            below = sum(x < total_rate for x in totals)
            equal = sum(x == total_rate for x in totals)
            score = round(100 * (1 - (below + 0.5 * equal) / peer_count), 1)
    return {
        'year': year, 'neighbourhood': target.get('AREA_NAME'),
        'hood_id': target.get('HOOD_ID'), 'counts': counts, 'rates_per_100k': rates,
        'total_selected_count': sum(counts.values()) if complete else None,
        'combined_selected_rate_per_100k': round(total_rate, 2) if total_rate is not None else None,
        'relative_indicator_score': score, 'peer_count': peer_count,
        'missing_categories': [c for c in CATEGORIES if c not in rates],
        'note': ('Relative TPS reported-crime-rate index, not a safety score or a probability of victimization. '
                 'Equal weighting across five selected indicators; categories may count offences/victims, not unique incidents. '
                 'Rates per 100,000 use TPS population estimates; neighbourhood-level counts are not a property-level measure. '
                 'Do not compare across police services, boundary revisions or partial years.'),
    }


def fetch_neighbourhood(lat, lon, year=None, *, client=None):
    if not (43.4 <= lat <= 44.1 and -80.0 <= lon <= -78.9):
        raise PoliceDataUnavailable('Location is outside the supported Toronto-area lookup bounds.')
    own = client is None
    if own:
        client = httpx.Client(timeout=httpx.Timeout(35, connect=8))
    try:
        meta = _get(client, LAYER, {'f': 'json'})
        names = {f['name'] for f in meta.get('fields', [])}
        years = available_years(names)
        if not years:
            raise PoliceDataUnavailable('TPS layer has no supported annual rate fields.')
        year = years[0] if year is None else int(year)
        if year not in years:
            raise PoliceDataUnavailable(f'Year {year} unavailable. Available: {years}')
        fields = resolve_fields(names, year)
        if len(fields) != len(CATEGORIES):
            raise PoliceDataUnavailable(f'Year {year} is missing required indicators: {set(CATEGORIES)-set(fields)}')
        columns = ['AREA_NAME', 'HOOD_ID'] + [name for pair in fields.values() for name in pair]
        params = {'f': 'json', 'where': '1=1', 'outFields': ','.join(columns),
                  'returnGeometry': 'false', 'geometryType': 'esriGeometryPoint',
                  'geometry': f'{lon},{lat}', 'inSR': '4326',
                  'spatialRel': 'esriSpatialRelIntersects', 'resultRecordCount': 2}
        match = _get(client, LAYER + '/query', params).get('features', [])
        if len(match) != 1:
            raise PoliceDataUnavailable('No unique Toronto Police neighbourhood polygon contains this location.')
        target = match[0]['attributes']
        peer_params = {'f': 'json', 'where': '1=1', 'outFields': ','.join(columns),
                       'returnGeometry': 'false', 'resultRecordCount': 2000}
        all_data = _get(client, LAYER + '/query', peer_params)
        if all_data.get('exceededTransferLimit'):
            raise PoliceDataUnavailable('Neighbourhood results truncated; index cannot be calculated.')
        peers = [f['attributes'] for f in all_data.get('features', [])]
        result = evaluate(target, peers, fields, year)
        result['source_url'] = LAYER
        return result
    finally:
        if own:
            client.close()
