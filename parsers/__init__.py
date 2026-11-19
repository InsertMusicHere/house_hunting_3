"""Paste/upload parsers. They read text the user supplies and never fetch anything."""
from __future__ import annotations

import re

from parsers import housesigma, realtor_ca
from parsers.common import ParsedListing

SUPPORTED_SITES = [realtor_ca.SITE, housesigma.SITE]
AUTO = "Auto-detect"
_PARSERS = {realtor_ca.SITE: realtor_ca.parse, housesigma.SITE: housesigma.parse}


def detect_site(text: str) -> str | None:
    rt = bool(re.search(r"MLS[^A-Za-z0-9]{0,8}Number", text)) + ("REALTOR.ca" in text) + ("Property Summary" in text)
    hs = (
        bool(re.search(r"Buy/sell history for", text))
        + bool(re.search(r"^Key facts for", text, re.M | re.I))
        + bool(re.search(r"^Listed for:", text, re.M))
        + ("HouseSigma" in text)
    )
    if rt > hs:
        return realtor_ca.SITE
    if hs > rt:
        return housesigma.SITE
    return None


def parse_text(text: str, site: str = AUTO) -> ParsedListing | None:
    """Parse with the chosen site's parser, or auto-detect. None if the layout is unknown."""
    chosen = detect_site(text) if site == AUTO else site
    parser = _PARSERS.get(chosen or "")
    return parser(text) if parser else None
