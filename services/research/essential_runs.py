"""Persist aggregate counts only, with source and location provenance."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from database.models import NeighbourhoodResearch, ResearchItem
from services.research.essentials import ATTRIBUTION


def save_run(session, prop, metrics, *, radius_m, source_url, feed_metadata=None):
    if prop.location_verified_at is None or prop.latitude is None or prop.longitude is None:
        raise ValueError('Location must be verified')
    now = datetime.now(timezone.utc)
    run = NeighbourhoodResearch(property_id=prop.id, run_at=now, radius_m=radius_m, status='success',
        params={'provider': 'essentials_6c', 'location': [prop.latitude, prop.longitude], 'location_verified_at': str(prop.location_verified_at), 'distance_method': 'straight_line_haversine', 'feed': feed_metadata or {}},
        provider_status={'overpass': 'success'})
    session.add(run)
    session.flush()
    for category, values in metrics.items():
        for suffix, value, unit in (('count', values['count'], 'mapped features'), ('nearest_m', values['nearest_m'], 'metres')):
            if value is None: continue
            session.add(ResearchItem(run_id=run.id, category='essentials', key=f'{category}_{suffix}',
                value_num=Decimal(str(value)), unit=unit, method='estimated', confidence='medium',
                source_name='OpenStreetMap / Overpass API', source_url=source_url,
                dataset_vintage='Live OSM snapshot; feature edits vary', retrieved_at=now,
                attribution=ATTRIBUTION, note='OSM proximity only; no maternity service, clinical safety, school designation or scheduled transit validation'))
    return run


def latest_run(session, property_id):
    return session.scalars(select(NeighbourhoodResearch).where(
        NeighbourhoodResearch.property_id == property_id,
        NeighbourhoodResearch.params['provider'].as_string() == 'essentials_6c',
        NeighbourhoodResearch.status == 'success').order_by(NeighbourhoodResearch.run_at.desc())).first()
