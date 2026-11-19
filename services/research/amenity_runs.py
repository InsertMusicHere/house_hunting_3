"""Persist aggregated amenity metrics and per-run provenance, not individual OSM features."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from database.models import NeighbourhoodResearch, ResearchItem
from services.research.amenities import ATTRIBUTION, CATEGORIES


def save_amenity_run(session, prop, summary: dict, *, radius_m: int, source_url: str,
                     fetched_at: datetime | None = None):
    if prop.location_verified_at is None or prop.latitude is None or prop.longitude is None:
        raise ValueError("Confirm the property's location before saving research")
    now = datetime.now(timezone.utc)
    observed = fetched_at or now
    run = NeighbourhoodResearch(
        property_id=prop.id, run_at=now, radius_m=radius_m, status="success",
        params={"provider": "overpass", "location": [prop.latitude, prop.longitude],
                "distance_method": "straight_line_haversine", "radii_m": [400, 800, 1200]},
        provider_status={"overpass": "success"},
    )
    session.add(run)
    session.flush()
    common = dict(run_id=run.id, category="amenities", method="estimated", confidence="medium",
                  source_name="OpenStreetMap / Overpass API", source_url=source_url,
                  dataset_vintage="Live OSM snapshot; individual edits have varying dates",
                  retrieved_at=observed, attribution=ATTRIBUTION,
                  note=summary["note"])
    session.add(ResearchItem(key="proximity_score", value_num=Decimal(str(summary["score"])),
                             unit="0-100", **common))
    for radius, values in summary["counts"].items():
        for category in CATEGORIES:
            session.add(ResearchItem(key=f"{category}_{radius}m", value_num=Decimal(values[category]),
                                     unit="mapped features", **common))
    return run


def latest_amenity_run(session, property_id):
    return session.scalars(select(NeighbourhoodResearch).where(
        NeighbourhoodResearch.property_id == property_id,
        NeighbourhoodResearch.params["provider"].as_string() == "overpass",
        NeighbourhoodResearch.status == "success",
    ).order_by(NeighbourhoodResearch.run_at.desc())).first()


def metrics_for_run(session, run_id):
    return {i.key: i for i in session.scalars(select(ResearchItem).where(ResearchItem.run_id == run_id)).all()}
