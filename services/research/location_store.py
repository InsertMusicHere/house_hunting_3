"""Location persistence and research history; transaction ownership belongs to caller."""
from datetime import datetime, timezone, timedelta
import uuid
from sqlalchemy import select
from database.models import Property, NeighbourhoodResearch, ResearchCache
from utils.normalization import normalize_postal_code, build_address_key
from services.geocoding_service import valid_coordinates

IDENTITY = ('address', 'unit', 'city', 'postal_code')


def invalidate_if_changed(prop, old_identity):
    if tuple(getattr(prop, k) for k in IDENTITY) != tuple(old_identity):
        prop.location_verified_at = None
        # Keep coordinates for audit and display as unverified, never silently reuse.


def confirm_location(prop, latitude, longitude, *, source, confidence, manual=False):
    if not valid_coordinates(latitude, longitude):
        raise ValueError('Coordinates must be valid latitude/longitude values')
    now = datetime.now(timezone.utc)
    prop.latitude, prop.longitude = latitude, longitude
    prop.geocode_source = source
    prop.geocode_confidence = confidence
    prop.geocoded_at = now
    prop.location_verified_at = now
    prop.location_is_manual = manual


def save_postal(session, prop, raw):
    postal = normalize_postal_code(raw)
    if not postal:
        raise ValueError('Enter a valid Ontario postal code')
    from database.repository import find_duplicates
    # Duplicate constraints are still enforced by PostgreSQL on flush.
    old = tuple(getattr(prop, k) for k in IDENTITY)
    prop.postal_code = postal
    prop.address_key = build_address_key(prop.address, prop.unit, postal)
    prop.manual_overrides = {**(prop.manual_overrides or {}), 'postal_code': True}
    invalidate_if_changed(prop, old)
    session.flush()


def begin_run(session, prop, *, radius_m=1500, params=None):
    row = NeighbourhoodResearch(property_id=prop.id, run_at=datetime.now(timezone.utc),
                                radius_m=radius_m, status='pending', params=params or {})
    session.add(row)
    session.flush()
    return row
