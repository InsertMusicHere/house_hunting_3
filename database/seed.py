"""Reference data: the sources reviewed in Phase 1."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.enums import TermsStatus
from database.models import DataSource

REVIEWED_ON = date(2026, 11, 15)

SOURCES = [
    {
        "name": "HouseSigma",
        "domain": "housesigma.com",
        "terms_url": "https://housesigma.com/blog-en/terms-of-use/",
        "terms_status": TermsStatus.NOT_PERMITTED.value,
        "terms_notes": "Terms read directly. Prohibits scraping and data mining; personal, "
        "non-commercial use only.",
    },
    {
        "name": "Realtor.ca",
        "domain": "realtor.ca",
        "terms_url": None,
        "terms_status": TermsStatus.NOT_PERMITTED.value,
        "terms_notes": "Realtor.ca's own terms page was not read directly. CREA states it "
        "sends cease-and-desist letters to scrapers of REALTOR.ca. Treated as not permitted.",
    },
    {
        "name": "Condos.ca",
        "domain": "condos.ca",
        "terms_url": "https://condos.ca/terms",
        "terms_status": TermsStatus.NOT_PERMITTED.value,
        "terms_notes": "Terms prohibit scraping and data mining; private, non-commercial use only.",
    },
    {
        "name": "Zolo",
        "domain": "zolo.ca",
        "terms_url": "https://www.zolo.ca/legal-terms",
        "terms_status": TermsStatus.NOT_PERMITTED.value,
        "terms_notes": "Terms list scraping and data mining as prohibited uses.",
    },
    {
        "name": "Manual entry",
        "domain": None,
        "terms_url": None,
        "terms_status": TermsStatus.UNREVIEWED.value,
        "terms_notes": "Details typed or pasted by the user.",
    },
]


def seed_data_sources(session: Session) -> int:
    """Insert missing sources (idempotent). Returns the number inserted."""
    inserted = 0
    for row in SOURCES:
        if session.scalar(select(DataSource).where(DataSource.name == row["name"])):
            continue
        session.add(DataSource(terms_reviewed_on=REVIEWED_ON, **row))
        inserted += 1
    session.flush()
    return inserted
