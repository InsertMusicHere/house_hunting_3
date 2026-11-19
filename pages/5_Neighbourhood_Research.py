"""Phase 6a: select a property, verify its location and inspect research history."""
import os
import time
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import streamlit as st

from auth.gate import require_login
from database.connection import get_engine
from database.models import Property, NeighbourhoodResearch
from services.geocoding_service import search_address, Candidate
from services.research.cache import key_for, read, write
from services.research.location_store import confirm_location, save_postal, begin_run
from services.research.phase6c_ui import render as render_phase6c
from services.research.phase6d_ui import render as render_phase6d

from dataclasses import asdict
from datetime import timedelta

from services.research.amenities import (
    ATTRIBUTION,
    CATEGORIES,
    OVERPASS_URL,
    Amenity,
    AmenityUnavailable,
    fetch_amenities,
    parse_elements,
    summarize,
)
from services.research.amenity_runs import (
    latest_amenity_run,
    metrics_for_run,
    save_amenity_run,
)


st.set_page_config(page_title='Neighbourhood Research', page_icon='📍', layout='wide')
require_login()
st.title('Neighbourhood Research')
st.caption(
    "Phase 6b: verified property location, nearby amenities "
    "and historical convenience estimates. Schools, transit "
    "frequency and safety research arrive in later phases."
)

with Session(get_engine()) as session:
    properties = session.scalars(select(Property).order_by(Property.city, Property.address)).all()
    if not properties:
        st.info('Add a property first.')
        st.stop()
    labels = {str(p.id): f'{p.address}, {p.city} — {p.postal_code or "postal code missing"}' for p in properties}
    st.dataframe([{'Property': labels[str(p.id)], 'Verified location': 'Yes' if p.location_verified_at else 'No'}
                  for p in properties], hide_index=True, width='stretch')
    selected = st.selectbox('Select one property', list(labels), format_func=lambda x: labels[x])
    prop = session.get(Property, __import__('uuid').UUID(selected))
    key = f'nr_{selected}_'
    if not prop.postal_code:
        st.warning('Postal code is missing. Save it before searching for the address.')
        postal = st.text_input('Ontario postal code', key=key+'postal')
        if st.button('Save postal code', key=key+'save_postal'):
            try:
                save_postal(session, prop, postal)
                session.commit()
                st.success('Postal code saved.')
                st.rerun()
            except (ValueError, IntegrityError) as exc:
                session.rollback()
                st.error(f'Could not save postal code: {exc}')
    if prop.latitude is not None and prop.longitude is not None:
        st.map([{'lat': prop.latitude, 'lon': prop.longitude}], latitude='lat', longitude='lon', zoom=14)
        st.write(f'Latitude: {prop.latitude:.6f} · Longitude: {prop.longitude:.6f}')
        st.caption(f'Source: {prop.geocode_source or "unknown"}; confidence: {prop.geocode_confidence or "unknown"}; '
                   f'verified: {prop.location_verified_at or "not verified"}')
    else:
        st.info('No coordinates saved yet.')

    st.subheader('Confirm or correct location')
    st.caption('Search is optional and user-triggered. Nominatim results are candidates, not verified coordinates.')
    if st.button('Find address coordinates', disabled=not bool(prop.postal_code), key=key+'search'):
        agent = os.getenv('NOMINATIM_USER_AGENT', '')
        if not agent:
            st.error('Set NOMINATIM_USER_AGENT to an identifying application name and contact email before searching.')
        else:
            cache_key = key_for('nominatim', {'address': prop.address, 'city': prop.city, 'postal': prop.postal_code})
            cached = read(session, cache_key)
            if cached is None:
                # Process-level rate limit for the single-admin deployment.
                last = st.session_state.get('nominatim_last_call', 0.0)
                wait = 1.1 - (time.monotonic() - last)
                if wait > 0:
                    time.sleep(wait)
                try:
                    found = search_address(prop.address or '', prop.city or '', prop.postal_code,
                                           user_agent=agent)
                    st.session_state['nominatim_last_call'] = time.monotonic()
                    cached = [c.__dict__ for c in found]
                    write(session, cache_key, 'nominatim', cached)
                    session.commit()
                except Exception as exc:
                    st.error(f'Geocoding unavailable: {exc}')
            if cached is not None:
                st.session_state[key+'candidates'] = cached
    candidates = st.session_state.get(key+'candidates', [])
    if candidates:
        choice = st.selectbox('Choose a candidate to verify', range(len(candidates)),
                              format_func=lambda i: candidates[i]['display_name'], key=key+'candidate')
        c = candidates[choice]
        st.caption(f"Precision: {c['precision']} · Confidence: {c['confidence']} · © OpenStreetMap contributors")
        st.map([{'lat': c['latitude'], 'lon': c['longitude']}], zoom=14)
        if st.button('Confirm selected location', key=key+'confirm'):
            confirm_location(prop, c['latitude'], c['longitude'], source='Nominatim / OpenStreetMap',
                             confidence=c['confidence'])
            session.commit()
            st.success('Location confirmed and saved.')
            st.rerun()
    elif key+'candidates' in st.session_state:
        st.warning('No matching Ontario candidates. Enter coordinates manually after verifying them on a map.')

    with st.expander('Manually set verified coordinates'):
        lat = st.number_input('Latitude', value=float(prop.latitude or 43.65), format='%.7f', key=key+'lat')
        lon = st.number_input('Longitude', value=float(prop.longitude or -79.38), format='%.7f', key=key+'lon')
        st.warning('The initial values are illustrative if this property has no saved coordinates. Verify the pin before saving.')
        if st.button('Save manually verified pin', key=key+'manual'):
            confirm_location(prop, lat, lon, source='Owner manual map verification',
                             confidence='manual', manual=True)
            session.commit()
            st.rerun()


    # ---------------------------------------------------------
    # Phase 6b: nearby amenities and convenience research
    # ---------------------------------------------------------
    st.divider()
    st.subheader("Nearby amenities")
    st.caption(
        "Find mapped groceries, pharmacies, parks, libraries, "
        "daycare facilities, cafés and bus stops. "
        "Distances are straight-line estimates, not walking routes."
    )

    location_ready = (
        prop.latitude is not None
        and prop.longitude is not None
        and prop.location_verified_at is not None
    )

    if not location_ready:
        st.info(
            "Confirm the property's location above before "
            "running neighbourhood research."
        )
    else:
        st.success("Verified property coordinates are available.")

        radius_m = st.selectbox(
            "Search radius",
            options=[1200, 1500, 2000, 3000],
            index=0,
            format_func=lambda r: f"{r:,} metres",
            key=key + "amenity_radius",
        )

        refresh_live = st.checkbox(
            "Force a fresh OpenStreetMap request",
            value=False,
            key=key + "amenity_force",
            help=(
                "Normally cached results are reused for up to "
                "seven days. A fresh request bypasses the cache."
            ),
        )

        if st.button(
            "Calculate neighbourhood convenience",
            type="primary",
            key=key + "calculate_amenities",
        ):
            try:
                lat = float(prop.latitude)
                lon = float(prop.longitude)


                last_request = st.session_state.get(
                    "overpass_last_request", 0.0
                )

                if refresh_live:
                    elapsed = time.monotonic() - last_request

                    if elapsed < 5:
                        raise AmenityUnavailable(
                            "Please wait a few seconds before "
                            "requesting fresh data again."
                        )

                    st.session_state[
                        "overpass_last_request"
                    ] = time.monotonic()

                cache_key = key_for(
                    "overpass_amenities_v1",
                    {
                        "latitude": round(lat, 7),
                        "longitude": round(lon, 7),
                        "radius_m": radius_m,
                    },
                )

                cached = (
                    None
                    if refresh_live
                    else read(session, cache_key)
                )

                if cached is None:
                    with st.spinner(
                        "Retrieving nearby amenities from OpenStreetMap..."
                    ):
                        response = fetch_amenities(
                            lat,
                            lon,
                            radius_m,
                        )
                        amenities = parse_elements(
                            response,
                            lat,
                            lon,
                            radius_m,
                        )

                    # Cache compact parsed amenities, not the full
                    # external API response.
                    cached = [asdict(a) for a in amenities]

                    write(
                        session,
                        cache_key,
                        "overpass_amenities_v1",
                        cached,
                        ttl_days=7,
                    )
                    session.commit()

                amenities = [
                    Amenity(**item)
                    for item in cached
                ]

                summary = summarize(amenities)

                run = save_amenity_run(
                    session,
                    prop,
                    summary,
                    radius_m=radius_m,
                    source_url=OVERPASS_URL,
                )
                session.commit()

                st.session_state[key + "amenity_result"] = {
                    "run_id": str(run.id),
                    "summary": summary,
                    "amenities": cached,
                }

                st.success(
                    "Neighbourhood research completed and "
                    "saved to Neon PostgreSQL."
                )

            except (
                AmenityUnavailable,
                ValueError,
                OSError,
            ) as exc:
                session.rollback()
                st.error(
                    f"Amenity research could not be completed: {exc}"
                )
            except Exception:
                session.rollback()
                st.error(
                    "An unexpected error occurred. No research "
                    "snapshot was saved."
                )
                raise
        

        current_location = (
            round(float(prop.latitude), 7),
            round(float(prop.longitude), 7),
            str(prop.location_verified_at),
        )

        previous_location = st.session_state.get(
            key + "amenity_location"
        )

        if previous_location != current_location:
            st.session_state.pop(
                key + "amenity_result", None
            )
            st.session_state[key + "amenity_location"] = (
                current_location
            )

        result = st.session_state.get(
            key + "amenity_result"
        )

        if result:
            summary = result["summary"]
            amenities = [
                Amenity(**item)
                for item in result["amenities"]
            ]

            st.markdown("### Convenience summary")

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Convenience score",
                f"{summary['score']:.1f}/100",
            )
            c2.metric(
                "Categories represented",
                (
                    f"{summary['covered_categories']}"
                    f"/{summary['total_categories']}"
                ),
            )
            c3.metric(
                "Mapped amenities",
                len(amenities),
            )

            st.info(summary["note"])

            st.markdown("**Amenities by distance**")

            table_rows = []

            for category in CATEGORIES:
                table_rows.append({
                    "Category": category.title(),
                    "Within 400 m": summary["counts"]["400"][category],
                    "Within 800 m": summary["counts"]["800"][category],
                    "Within 1,200 m": summary["counts"]["1200"][category],
                })

            st.dataframe(
                table_rows,
                hide_index=True,
                width='stretch',
            )

            if amenities:
                st.markdown("**Nearby mapped locations**")

                map_rows = [{
                    "lat": float(prop.latitude),
                    "lon": float(prop.longitude),
                    "category": "Selected property",
                }]

                map_rows.extend({
                    "lat": a.latitude,
                    "lon": a.longitude,
                    "category": a.category,
                } for a in amenities)

                st.map(
                    map_rows,
                    latitude="lat",
                    longitude="lon",
                    zoom=13,
                )

                st.dataframe(
                    [{
                        "Category": a.category.title(),
                        "Name": a.name or "Unnamed OSM feature",
                        "Straight-line distance (m)": round(
                            a.distance_m
                        ),
                        "OSM reference": (
                            f"{a.osm_type}/{a.osm_id}"
                        ),
                    } for a in amenities],
                    hide_index=True,
                    width='stretch',
                )
            else:
                st.warning(
                    "No matching mapped amenities were returned. "
                    "This is not evidence that the area has none."
                )

            st.caption(ATTRIBUTION)
            st.link_button(
                "OpenStreetMap data source",
                "https://www.openstreetmap.org/copyright",
            )

        # Load the latest persisted snapshot even after a
        # browser refresh or Streamlit session restart.
        latest = latest_amenity_run(session, prop.id)

        if latest:
            metrics = metrics_for_run(session, latest.id)
            score_item = metrics.get("proximity_score")

            if score_item is not None:
                st.markdown("### Latest saved research")

                st.write(
                    f"Last researched: {latest.run_at}"
                )
                st.metric(
                    "Saved convenience score",
                    f"{float(score_item.value_num):.1f}/100",
                )

                saved_rows = []
                for category in CATEGORIES:
                    row = {"Category": category.title()}

                    for distance in (400, 800, 1200):
                        item = metrics.get(
                            f"{category}_{distance}m"
                        )
                        row[f"{distance} m"] = (
                            int(item.value_num)
                            if item is not None
                            else None
                        )

                    saved_rows.append(row)

                st.dataframe(
                    saved_rows,
                    hide_index=True,
                    width='stretch',
                )

                st.caption(
                    "Saved snapshot: "
                    f"{score_item.source_name} | "
                    f"Method: {score_item.method} | "
                    f"Retrieved: {score_item.retrieved_at}"
                )
                st.caption(
                    score_item.attribution or ATTRIBUTION
                )

                render_phase6c(session, prop, key)
                render_phase6d(session, prop, key)

    st.subheader('Research history')
    history = session.scalars(select(NeighbourhoodResearch).where(
        NeighbourhoodResearch.property_id == prop.id).order_by(NeighbourhoodResearch.run_at.desc())).all()
    if history:
        st.dataframe([{'Run': r.run_at, 'Status': r.status, 'Radius (m)': r.radius_m}
                      for r in history], hide_index=True)
    else:
        st.caption(
            "No research runs yet. Calculate neighbourhood "
            "convenience above to create the first snapshot."
        )
    st.caption('Map data © OpenStreetMap contributors. Location confirmation is not a rooftop accuracy guarantee.')
