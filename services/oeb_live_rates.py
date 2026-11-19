"""Fetch current OEB-published energy commodity prices, never pretend they are full bills.

Residential Tiered RPP electricity and gas *supply* prices only. Delivery,
regulatory charges, taxes, credits and retailer contracts are excluded.
A user must select their actual gas rate zone; a postal code is insufficient.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ELECTRICITY_URL = 'https://www.oeb.ca/consumer-information-and-protection/electricity-rates'
GAS_URL = 'https://oeb.ca/rates-and-your-bill/natural-gas-rates'
GAS_ZONES = (
    'Enbridge Gas Inc. - Union South Rate Zone',
    'Enbridge Gas Inc. - Union North East Rate Zone',
    'Enbridge Gas Inc. - Union North West Rate Zone',
    'Enbridge Gas Inc.',
    'EPCOR Natural Gas Limited Partnership (Aylmer)',
    'EPCOR Natural Gas Limited Partnership (South Bruce)',
)
CENT = Decimal('0.01')

class RateUnavailable(ValueError):
    pass

@dataclass(frozen=True)
class PublishedRate:
    unit: str
    rate_cents: Decimal
    effective: str
    source_url: str
    fetched_utc: str


def _text(url: str, timeout: float = 12) -> str:
    request = Request(url, headers={
        'User-Agent': 'OntarioHomeBudget/1.0 (personal-use tariff research)',
        'Accept': 'text/html',
    })
    with urlopen(request, timeout=timeout) as response:
        html = response.read(2_000_000).decode('utf-8', errors='replace')
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer']):
        tag.decompose()
    return ' '.join(soup.stripped_strings)


def _effective(text: str, month: str) -> str:
    match = re.search(rf'{month}\s+\d{{1,2}},?\s+20\d{{2}}', text, re.I)
    if not match:
        raise RateUnavailable('OEB effective date could not be verified; refusing to use an unverified rate.')
    return match.group(0)


def _rate_after(text: str, marker: str, *, window: int = 300) -> Decimal:
    pos = text.casefold().find(marker.casefold())
    if pos < 0:
        raise RateUnavailable(f'OEB rate label missing: {marker}')
    fragment = text[pos + len(marker):pos + len(marker) + window]
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:¢|cents?)', fragment, re.I)
    if not match:
        raise RateUnavailable(f'OEB price missing after: {marker}')
    return Decimal(match.group(1))


def fetch_tiered_electricity() -> tuple[PublishedRate, PublishedRate]:
    """Current OEB RPP Tiered energy-only prices, in cents/kWh.

    Fail closed if the published page structure changes. This is NOT a bill quote.
    """
    text = _text(ELECTRICITY_URL)
    dates = re.findall(r'(?:November|May)\s+\d{1,2},?\s+20\d{2}', text, re.I)
    if not dates:
        raise RateUnavailable('Cannot verify effective date of electricity rates.')
    # Search the *current prices* section; do not parse historic rate tables.
    start = text.casefold().find('current prices')
    if start < 0:
        raise RateUnavailable('Current prices section not found.')
    section = text[start: start + 8000]
    tier = section.casefold().find('tiered')
    if tier < 0:
        raise RateUnavailable('Tiered price plan not found.')
    tier_text = section[tier:]
    tier1 = _rate_after(tier_text, 'Tier 1', window=350)
    tier2 = _rate_after(tier_text, 'Tier 2', window=350)
    fetched = datetime.now(timezone.utc).isoformat()
    return (PublishedRate('kWh', tier1, dates[0], ELECTRICITY_URL, fetched),
            PublishedRate('kWh', tier2, dates[0], ELECTRICITY_URL, fetched))


def fetch_gas_supply(zone: str) -> PublishedRate:
    """OEB current gas supply rate only; zone must be confirmed by the user."""
    if zone not in GAS_ZONES:
        raise RateUnavailable('Choose a supported gas utility and rate zone.')
    text = _text(GAS_URL)
    match = re.search(r'Current gas rates for supply\s*\(([^)]*20\d{2})\)', text, re.I)
    if not match:
        raise RateUnavailable('Cannot verify current gas supply effective date.')
    # Constrain search to current-rate table, not explanatory mentions below it.
    table_start = match.end()
    table_end = text.casefold().find('how often does', table_start)
    section = text[table_start:table_end if table_end > 0 else table_start + 6000]
    # Zone names overlap (Enbridge base name vs Union zones): locate the exact
    # selected row by finding a numeric price immediately after the name.
    pattern = re.escape(zone) + r'\s*(?:\*+)?\s*(\d+(?:\.\d+)?)\s*(?:¢|cents?)\s*/\s*m'
    result = re.search(pattern, section, re.I)
    if not result:
        raise RateUnavailable('Could not verify a rate for this gas zone on the OEB page.')
    return PublishedRate('m³', Decimal(result.group(1)), match.group(1), GAS_URL,
                         datetime.now(timezone.utc).isoformat())


def tiered_energy_cost(kwh: Decimal, tier1: PublishedRate, tier2: PublishedRate,
                       *, winter: bool) -> Decimal:
    if kwh < 0 or not kwh.is_finite():
        raise ValueError('kWh must be finite and non-negative')
    threshold = Decimal('1000' if winter else '600')
    cents = min(kwh, threshold) * tier1.rate_cents + max(kwh - threshold, 0) * tier2.rate_cents
    return (cents / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def gas_supply_cost(m3: Decimal, rate: PublishedRate) -> Decimal:
    if m3 < 0 or not m3.is_finite():
        raise ValueError('m³ must be finite and non-negative')
    return (m3 * rate.rate_cents / 100).quantize(CENT, rounding=ROUND_HALF_UP)
