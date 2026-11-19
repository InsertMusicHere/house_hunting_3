"""Auditable ward aggregate import. No incident-level data or invented denominators."""
from __future__ import annotations
import csv
import io
import math
from urllib.parse import urlparse

CATEGORIES = ('assault', 'auto_theft', 'break_enter', 'robbery', 'theft_over')
FIELDS = ('municipality', 'ward', 'year', 'population', 'source_url', *CATEGORIES)

class WardDataError(ValueError):
    pass

def parse_ward_csv(content: bytes) -> list[dict]:
    if len(content) > 2_000_000:
        raise WardDataError('File exceeds 2 MB limit')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise WardDataError('CSV must be UTF-8') from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
        raise WardDataError('Required columns: ' + ', '.join(FIELDS))
    result, seen = [], set()
    for row in reader:
        if not any(row.values()):
            continue
        municipality = (row['municipality'] or '').strip().lower()
        if municipality not in ('mississauga', 'brampton'):
            raise WardDataError('Only Mississauga or Brampton ward data is supported')
        try:
            ward = int(row['ward'])
            year = int(row['year'])
            population = int(row['population'])
            counts = {c: int(row[c]) for c in CATEGORIES}
        except (TypeError, ValueError) as exc:
            raise WardDataError('Ward, year, population and counts must be integers') from exc
        if not (1 <= ward <= 50 and 2010 <= year <= 2100 and population > 0 and all(v >= 0 for v in counts.values())):
            raise WardDataError('Invalid ward, year, population or negative counts')
        source = (row['source_url'] or '').strip()
        parsed = urlparse(source)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise WardDataError('Each row requires a valid HTTPS source_url')
        key = (municipality, ward, year)
        if key in seen:
            raise WardDataError('Duplicate municipality/ward/year')
        seen.add(key)
        result.append(dict(municipality=municipality, ward=ward, year=year,
                           population=population, counts=counts, source_url=source))
    if not result:
        raise WardDataError('CSV contains no records')
    return result

def evaluate_ward(rows: list[dict], municipality: str, ward: int, year: int) -> dict:
    """Within-municipality, same-year midrank index from five summed per-capita rates."""
    peers = [r for r in rows if r['municipality'] == municipality.lower() and r['year'] == year]
    target = next((r for r in peers if r['ward'] == ward), None)
    if target is None:
        raise WardDataError('Selected ward/year is missing from uploaded data')
    # Five complete indicators and denominators are required. These are rates of
    # selected offence categories, not unique victims or overall crime severity.
    def total(r):
        return sum(r['counts'][c] for c in CATEGORIES) * 100_000 / r['population']
    totals = [total(r) for r in peers]
    target_total = total(target)
    # Avoid giving an apparently precise rank to a small or incomplete peer set.
    score = None
    if len(peers) >= 8:
        below = sum(x < target_total for x in totals)
        equal = sum(math.isclose(x, target_total, rel_tol=1e-12) for x in totals)
        score = round(100 * (1 - (below + equal / 2) / len(peers)), 1)
    return dict(neighbourhood=f'{municipality.title()} Ward {ward}', year=year,
                geographic_scope='municipal ward', municipality=municipality.title(), ward=ward,
                population=target['population'], counts=target['counts'],
                rates_per_100k={c: round(target['counts'][c]*100_000/target['population'], 2) for c in CATEGORIES},
                combined_selected_rate_per_100k=round(target_total, 2),
                relative_indicator_score=score, peer_count=len(peers),
                source_url=target['source_url'],
                note=('Owner-imported ward aggregate data; verify official source, ward boundaries, reporting period, '
                      'population vintage and offence definitions. Relative same-municipality index, NOT a personal safety score. '
                      'Never compare with Toronto index or infer property-level risk.'))
