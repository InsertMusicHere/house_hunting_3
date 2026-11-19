"""Peel Regional Police published region-wide full-year counts.

Not neighbourhood incident counts or a property-level safety rating.
"""
from __future__ import annotations
from html.parser import HTMLParser
import re
import httpx

SOURCE = 'https://www.peelpolice.ca/news/crime-statistics-and-maps/'
DASHBOARD = 'https://www.peelpolice.ca/news/community-data-platform/'
CATEGORIES = {
    'robbery': ('robberies',),
    'break_enter': ('break and enters', 'break and enter'),
    'vehicle_theft': ('theft of motor vehicles',),
    'theft_from_vehicle': ('theft from motor vehicles',),
    'sexual_violations': ('sexual violations',),
    'mischief': ('mischief',),
    'homicide_occurrences': ('homicide occurrences',),
}

class PeelDataUnavailable(RuntimeError):
    pass

class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.depth = 0
    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            if self.depth == 0:
                self.table = []
            self.depth += 1
        elif self.depth == 1 and tag == 'tr':
            self.row = []
        elif self.depth == 1 and tag in ('td', 'th') and self.row is not None:
            self.cell = []
    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.table is not None:
                self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.tables.append(self.table)
                self.table = None

def parse_full_year_counts(html: str, year: int | None = None) -> dict:
    parser = _Tables()
    parser.feed(html)
    annual = {}
    for table in parser.tables:
        if not table:
            continue
        header = next((row for row in table if any(re.search(r'20\d{2}\s+full\s+year', c, re.I) for c in row)), None)
        if header is None:
            continue
        year_columns = [(i, int(m.group(1))) for i, text in enumerate(header)
                        if (m := re.search(r'(20\d{2})\s+full\s+year', text, re.I))]
        for row in table:
            if not row:
                continue
            label = re.sub(r'\s+', ' ', row[0].lower()).strip()
            category = next((key for key, names in CATEGORIES.items() if label in names), None)
            if category is None:
                continue
            for col, yr in year_columns:
                if col >= len(row):
                    continue
                raw = row[col].replace(',', '').strip()
                if raw.isdigit():
                    annual.setdefault(yr, {})[category] = int(raw)
    if not annual:
        raise PeelDataUnavailable('No recognized full-year Peel Police statistics tables were found; page format may have changed.')
    selected = max(annual) if year is None else int(year)
    if selected not in annual:
        raise PeelDataUnavailable(f'Full year {selected} not found. Available years: {sorted(annual)}')
    counts = annual[selected]
    if len(counts) < 3:
        raise PeelDataUnavailable('Too few recognized categories; refusing to save an incomplete scrape.')
    return {
        'provider': 'prp', 'geographic_scope': 'Peel Regional Police service area (region-wide)',
        'year': selected, 'neighbourhood': 'Peel Region (not property neighbourhood)',
        'counts': counts, 'rates_per_100k': {}, 'relative_indicator_score': None,
        'peer_count': 0, 'source_url': SOURCE, 'dashboard_url': DASHBOARD,
        'note': ('Published Peel-wide full-year category counts, not Mississauga or neighbourhood counts. '
                 'Categories may use different counting units and must not be summed as unique incidents. '
                 'No local safety score is calculated. The community dashboard uses offset locations '
                 'and is informational, not official statistics.'),
    }

def fetch_peel_region(year: int | None = None, *, client: httpx.Client | None = None) -> dict:
    own = client is None
    if own:
        client = httpx.Client(timeout=httpx.Timeout(35, connect=8), follow_redirects=True,
                              headers={'User-Agent': 'HousingNeighbourhoodResearch/1.0 (personal research)'})
    try:
        try:
            response = client.get(SOURCE)
            response.raise_for_status()
            return parse_full_year_counts(response.text, year)
        except httpx.HTTPError as exc:
            raise PeelDataUnavailable(f'Peel Police statistics unavailable: {exc}') from exc
    finally:
        if own:
            client.close()
