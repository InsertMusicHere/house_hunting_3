"""DB-backed cache for third-party geocoding requests."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from sqlalchemy.orm import Session
from database.models import ResearchCache


def key_for(provider: str, payload: dict) -> str:
    raw = json.dumps([provider, payload], sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def read(session: Session, key: str):
    row = session.get(ResearchCache, key)
    if row is None or row.expires_at <= datetime.now(timezone.utc):
        return None
    return row.response_json


def write(session: Session, key: str, provider: str, response: list | dict, *, ttl_days=30):
    now = datetime.now(timezone.utc)
    row = session.get(ResearchCache, key)
    if row is None:
        row = ResearchCache(cache_key=key, provider=provider)
        session.add(row)
    row.response_json = response
    row.fetched_at = now
    row.expires_at = now + timedelta(days=ttl_days)
