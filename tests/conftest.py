"""Test database fixture.

By default tests run on in-memory SQLite (fast, no setup). To also test against real
PostgreSQL (JSONB, partial indexes), set TEST_DATABASE_URL to an EMPTY scratch database:

    $env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost/scratch"
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database.models import Base


@pytest.fixture()
def session():
    url = os.environ.get("TEST_DATABASE_URL")
    engine = create_engine(url) if url else create_engine("sqlite:///:memory:")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
        s.rollback()
    Base.metadata.drop_all(engine)
    engine.dispose()
