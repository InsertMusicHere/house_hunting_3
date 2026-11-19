"""Overpass amenity discovery. No calls until explicitly invoked by the page.

Distances are straight-line (haversine), NOT walking routes or isochrones.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
import os
import time

import httpx

OVERPASS_URL = "https://ovp.dresel.systems/api-na/interpreter"
OVERPASS_FALLBACK_URL = (
    "https://overpass.private.coffee/api/interpreter"
)
ATTRIBUTION = "© OpenStreetMap contributors (ODbL); data retrieved via Overpass API"
CATEGORIES = ("grocery", "pharmacy", "park", "library", "daycare", "cafe", "transit")
# Public transport is intentionally not counted as scheduled transit quality.
FILTERS = {
    "grocery": '["shop"~"^(supermarket|grocery|convenience)$"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "park": '["leisure"="park"]',
    "library": '["amenity"="library"]',
    "daycare": '["amenity"="kindergarten"]',
    "cafe": '["amenity"="cafe"]',
    "transit": '["highway"="bus_stop"]',
}


class AmenityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Amenity:
    osm_type: str
    osm_id: int
    category: str
    name: str | None
    latitude: float
    longitude: float
    distance_m: float


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a = sin(radians(lat2-lat1)/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(radians(lon2-lon1)/2)**2
    return 6371008.8 * 2 * asin(min(1.0, sqrt(a)))



def build_query(lat: float, lon: float, radius_m: int) -> str:
    """Build one compact Overpass query for all amenity categories."""
    if not (
        -90 <= lat <= 90
        and -180 <= lon <= 180
        and 100 <= radius_m <= 3000
    ):
        raise ValueError("Invalid location or search radius")

    # One selector for the amenity keys we need, rather than
    # 21 independent node/way/relation queries.
    return f"""
        [out:json][timeout:40];
        (
        nwr(around:{radius_m},{lat:.7f},{lon:.7f})
            ["shop"~"^(supermarket|grocery|convenience)$"];
        nwr(around:{radius_m},{lat:.7f},{lon:.7f})
            ["amenity"~"^(pharmacy|library|kindergarten|cafe)$"];
        nwr(around:{radius_m},{lat:.7f},{lon:.7f})
            ["leisure"="park"];
        nwr(around:{radius_m},{lat:.7f},{lon:.7f})
            ["highway"="bus_stop"];
        );
        out center;
        """.strip()



def parse_elements(payload: dict, lat: float, lon: float, radius_m: int) -> list[Amenity]:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise AmenityUnavailable("Overpass response has no elements array")
    output = []
    seen = set()
    for item in payload["elements"]:
        if not isinstance(item, dict):
            continue
        tags = item.get("tags") or {}
        coords = item.get("center") or item
        try:
            a, b = float(coords["lat"]), float(coords["lon"])
            osm_id, osm_type = int(item["id"]), item["type"]
        except (ValueError, TypeError, KeyError):
            continue
        if not (-90 <= a <= 90 and -180 <= b <= 180):
            continue
        distance = haversine_m(lat, lon, a, b)
        if distance > radius_m:
            continue
        for category, predicate in (
            ("grocery", tags.get("shop") in {"supermarket", "grocery", "convenience"}),
            ("pharmacy", tags.get("amenity") == "pharmacy"),
            ("park", tags.get("leisure") == "park"),
            ("library", tags.get("amenity") == "library"),
            ("daycare", tags.get("amenity") == "kindergarten"),
            ("cafe", tags.get("amenity") == "cafe"),
            ("transit", tags.get("highway") == "bus_stop"),
        ):
            key = (osm_type, osm_id, category)
            if predicate and key not in seen:
                seen.add(key)
                output.append(Amenity(osm_type, osm_id, category, tags.get("name"), a, b, round(distance, 1)))
    return sorted(output, key=lambda x: (x.distance_m, x.category, x.osm_id))



def fetch_amenities(
    lat: float,
    lon: float,
    radius_m: int = 1500,
    *,
    user_agent: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Fetch amenities with one fallback for temporary server failures."""
    agent = user_agent or os.getenv("NOMINATIM_USER_AGENT", "")

    if not agent or "example.com" in agent:
        raise AmenityUnavailable(
            "Set NOMINATIM_USER_AGENT to an identifying "
            "application name and real contact."
        )

    query = build_query(lat, lon, radius_m)

    owns_client = client is None

    if owns_client:
        client = httpx.Client(
            timeout=httpx.Timeout(
                50.0,
                connect=5.0,
                read=45.0,
                write=10.0,
                pool=5.0,
            )
        )

    endpoints = (OVERPASS_URL, OVERPASS_FALLBACK_URL)
    errors = []

    try:
        for index, endpoint in enumerate(endpoints):
            try:
                response = client.post(
                    endpoint,
                    data={"data": query},
                    headers={
                        "User-Agent": agent,
                        "Accept": "application/json",
                    },
                )

                response.raise_for_status()
                data = response.json()

                if not isinstance(data, dict):
                    raise AmenityUnavailable(
                        "Overpass returned an unexpected response."
                    )

                if data.get("remark"):
                    raise AmenityUnavailable(
                        f"Overpass reported: {data['remark']}"
                    )

                if not isinstance(data.get("elements"), list):
                    raise AmenityUnavailable(
                        "Overpass response has no elements array."
                    )

                return data

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code

                errors.append(
                    f"{endpoint}: HTTP {status}"
                )

                # Authentication, invalid requests and rate limits
                # should not trigger immediate fallback attempts.
                if status in (400, 401, 403, 406, 429):
                    break

                if status not in (500, 502, 503, 504):
                    break

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                ValueError,
                AmenityUnavailable,
            ) as exc:
                errors.append(
                    f"{endpoint}: {type(exc).__name__}: {exc}"
                )

            # Avoid immediately sending requests to two services.
            if index < len(endpoints) - 1:
                time.sleep(2)

        raise AmenityUnavailable(
            "All available Overpass attempts failed. "
            + " | ".join(errors)
        )

    finally:
        if owns_client:
            client.close()



def summarize(amenities: list[Amenity], *, radii_m=(400, 800, 1200)) -> dict:
    """Transparent straight-line proximity index; never a walking-time score."""
    counts = {str(r): {c: sum(a.category == c and a.distance_m <= r for a in amenities)
                        for c in CATEGORIES} for r in radii_m}
    # 7 equally weighted essentials; presence at <=1200m yields 1 point/category.
    present = sum(counts[str(radii_m[-1])][c] > 0 for c in CATEGORIES)
    return {"counts": counts, "score": round(100 * present / len(CATEGORIES), 1),
            "covered_categories": present, "total_categories": len(CATEGORIES),
            "method": "estimated", "distance_method": "straight_line_haversine",
            "note": "Straight-line proximity index, not a 15-minute walking isochrone. "
                    "OSM coverage can be incomplete; missing mapped amenities do not prove absence."}
