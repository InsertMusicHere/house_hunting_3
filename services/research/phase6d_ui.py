"""Phase 6d: police neighbourhood aggregates and Phase 6e: sourced due-diligence log."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import streamlit as st
from sqlalchemy import select
from database.models import NeighbourhoodResearch, ResearchItem
from services.research.cache import key_for, read, write
from services.research.police_stats import fetch_neighbourhood, PoliceDataUnavailable, SOURCE, CATEGORIES

from services.research.peel_stats import (
    fetch_peel_region,
    PeelDataUnavailable,
    SOURCE as PEEL_SOURCE,
    DASHBOARD as PEEL_DASHBOARD,
)

from services.research.peel_ward_ui import render_ward_import

CHECKS = {
    'Flood and stormwater': 'https://trca.ca/',
    'Environmental contamination': 'https://www.ontario.ca/page/environmental-site-registry',
    'Development applications': 'https://www.toronto.ca/city-government/planning-development/application-information-centre/',
    'Zoning and land use': 'https://www.toronto.ca/city-government/planning-development/zoning-by-law-preliminary-zoning-reviews/',
    'Road noise and construction': 'https://www.toronto.ca/services-payments/streets-parking-transportation/road-restrictions-closures/',
    'Air quality': 'https://www.airqualityontario.com/',
    'Visit at day and night': 'https://www.toronto.ca/',
}


def _save(session, prop, provider, category, items, params):
    now = datetime.now(timezone.utc)
    run = NeighbourhoodResearch(
        property_id=prop.id, run_at=now, radius_m=0, status='success',
        params={'provider': provider, 'location': [prop.latitude, prop.longitude],
                'location_verified_at': str(prop.location_verified_at), **params},
        provider_status={provider: 'recorded'},
    )
    session.add(run)
    session.flush()
    for entry in items:
        session.add(ResearchItem(run_id=run.id, category=category, **entry))
    session.commit()
    return run


def _recent(session, prop, provider):
    runs = session.scalars(select(NeighbourhoodResearch).where(
        NeighbourhoodResearch.property_id == prop.id,
        NeighbourhoodResearch.params['provider'].as_string() == provider,
        NeighbourhoodResearch.status == 'success',
    ).order_by(NeighbourhoodResearch.run_at.desc()).limit(20)).all()
    return runs


def _current(run, prop):
    params = run.params or {}
    return (params.get('location') == [prop.latitude, prop.longitude]
            and params.get('location_verified_at') == str(prop.location_verified_at))


def render(session, prop, key):
    st.divider()
    st.subheader('Police statistics, neighbourhood context & due diligence — Phases 6d–6e')
    if prop.location_verified_at is None or prop.latitude is None or prop.longitude is None:
        st.info('Verify property coordinates before researching police statistics or hazards.')
        return
    safety, context = st.tabs(['Police neighbourhood statistics', 'Hazards, development & visits'])
    
    with safety:
        st.caption(
            "Toronto Police neighbourhood statistics and "
            "Peel Regional Police published annual statistics. "
            "No individual incident locations are retrieved."
        )

        city = (prop.city or "").strip().lower()

        if city in ("mississauga", "brampton"):
            provider = "peel"
        elif city in ("toronto", "etobicoke", "scarborough",
                      "north york", "york", "east york"):
            provider = "toronto"
        else:
            provider = "unsupported"

        if provider == "peel":
            st.info(
                "Peel Regional Police selected from the "
                "property's municipality. Published annual "
                "figures cover the wider Peel service area, "
                "not this property's neighbourhood."
            )
            st.markdown(
                f"[Official Peel crime statistics]({PEEL_SOURCE})"
            )
            st.markdown(
                f"[Peel community crime dashboard]({PEEL_DASHBOARD})"
            )
        elif provider == "toronto":
            st.caption(
                "Toronto Police selected from the property's "
                "municipality. The official neighbourhood "
                "polygon will be checked during retrieval."
            )
            st.markdown(
                f"[Official Toronto Police open data]({SOURCE})"
            )
        else:
            st.warning(
                "No supported police provider is configured "
                "for this municipality. Check the property "
                "city and jurisdiction."
            )

        if st.button(
            "Fetch local police statistics",
            key=key + "police_fetch",
            disabled=provider == "unsupported",
        ):
            try:
                lat = float(prop.latitude)
                lon = float(prop.longitude)

                if provider == "peel":
                    cache_name = "prp_region_annual_v1"
                    run_provider = "prp_region_6d1"
                    source_name = "Peel Regional Police"
                    result = None

                    ck = key_for(
                        cache_name,
                        {"dataset": "published_full_year"},
                    )
                    result = read(session, ck)

                    if result is None:
                        with st.spinner(
                            "Retrieving Peel Regional Police "
                            "published annual statistics..."
                        ):
                            result = fetch_peel_region()

                        write(
                            session, ck, cache_name,
                            result, ttl_days=14,
                        )
                        session.commit()

                    vintage = f"Full year {result['year']}"
                    attribution = "Peel Regional Police"

                else:
                    cache_name = "tps_neighbourhood_6d_v1"
                    run_provider = "tps_neighbourhood_6d"
                    source_name = "Toronto Police Service"

                    ck = key_for(
                        cache_name,
                        {
                            "lat": round(lat, 6),
                            "lon": round(lon, 6),
                        },
                    )
                    result = read(session, ck)

                    if result is None:
                        with st.spinner(
                            "Retrieving Toronto Police "
                            "neighbourhood statistics..."
                        ):
                            result = fetch_neighbourhood(lat, lon)

                        write(
                            session, ck, cache_name,
                            result, ttl_days=14,
                        )
                        session.commit()

                    vintage = f"Full year {result['year']}"
                    attribution = (
                        "Toronto Police Service; "
                        "Open Government Licence – Ontario"
                    )

                now = datetime.now(timezone.utc)

                common = dict(
                    method="measured",
                    confidence="medium",
                    source_name=source_name,
                    source_url=result["source_url"],
                    dataset_vintage=vintage,
                    retrieved_at=now,
                    attribution=attribution,
                    note=result["note"],
                )

                entries = [
                    dict(
                        key="neighbourhood_police_summary",
                        value_json=result,
                        **common,
                    )
                ]

                score = result.get(
                    "relative_indicator_score"
                )

                if score is not None:
                    entries.append(
                        dict(
                            key="relative_indicator_score",
                            value_num=Decimal(str(score)),
                            unit="0-100 relative index",
                            **common,
                        )
                    )

                _save(
                    session,
                    prop,
                    run_provider,
                    "police_statistics",
                    entries,
                    {
                        "year": result["year"],
                        "neighbourhood": result["neighbourhood"],
                        "geographic_scope": result.get(
                            "geographic_scope",
                            "TPS neighbourhood",
                        ),
                    },
                )

                st.success(
                    f"{source_name} research snapshot "
                    "saved to Neon."
                )

            except (
                PoliceDataUnavailable,
                PeelDataUnavailable,
                ValueError,
            ) as exc:
                session.rollback()
                st.error(str(exc))
            except Exception as exc:
                session.rollback()
                st.error(
                    f"Unable to save police research: {exc}"
                )

        providers_to_show = (
            ["prp_region_6d1"]
            if provider == "peel"
            else ["tps_neighbourhood_6d"]
            if provider == "toronto"
            else []
        )

        for saved_provider in providers_to_show:
            runs = _recent(
                session, prop, saved_provider
            )

            if not runs:
                st.info(
                    "No police statistics saved for "
                    "this provider yet."
                )
                continue

            run = runs[0]
            items = session.scalars(
                select(ResearchItem).where(
                    ResearchItem.run_id == run.id
                )
            ).all()

            summary_item = next(
                (
                    item for item in items
                    if item.key == "neighbourhood_police_summary"
                ),
                None,
            )

            if summary_item is None:
                continue

            data = summary_item.value_json or {}

            if not _current(run, prop):
                st.warning(
                    "This snapshot belongs to an earlier "
                    "location verification. Refresh before "
                    "using it."
                )

            st.markdown(
                f"**{data.get('neighbourhood', 'Unknown area')}**"
                f" — {data.get('year', 'Unknown year')}"
            )
            st.caption(
                f"Retrieved: {summary_item.retrieved_at}"
            )

            score = data.get(
                "relative_indicator_score"
            )

            if score is not None:
                st.metric(
                    "Relative reported-crime-rate index "
                    "(not a safety rating)",
                    f"{float(score):.1f}/100",
                )
                st.caption(
                    "Compared with "
                    f"{data.get('peer_count', 0)} "
                    "Toronto Police neighbourhoods."
                )
            else:
                st.info(
                    "No neighbourhood-level relative index "
                    "is available for this dataset."
                )

            counts = data.get("counts") or {}
            rates = data.get("rates_per_100k") or {}

            if saved_provider == "tps_neighbourhood_6d":
                categories = CATEGORIES
            else:
                categories = tuple(counts)

            st.dataframe(
                [
                    {
                        "Indicator": category.replace(
                            "_", " "
                        ).title(),
                        "Published count": counts.get(category),
                        "Rate per 100,000": rates.get(category),
                    }
                    for category in categories
                ],
                hide_index=True,
                width="stretch",
            )

            st.caption(data.get("note", ""))
            st.markdown(
                f"[Dataset and methodology]"
                f"({data.get('source_url', SOURCE)})"
            )

            if saved_provider == "prp_region_6d1":
                st.markdown(
                    f"[Explore geographic crime patterns]"
                    f"({PEEL_DASHBOARD})"
                )

            if len(runs) > 1:
                st.caption(
                    f"{len(runs)} historical police "
                    "snapshots available."
                )
                
            render_ward_import(session, prop, key, _save)

    with context:
        st.caption('These are owner-reviewed due-diligence checks, not automated flood, contamination or construction determinations. No overall safety rating is inferred.')
        for name, url in CHECKS.items():
            st.markdown(f'[{name} — official starting point]({url})')
        with st.form(key+'due_diligence_form'):
            category = st.selectbox('Research topic', list(CHECKS), key=key+'due_topic')
            status = st.selectbox('Assessment', ['Not checked', 'Needs follow-up', 'Concern identified', 'No concern found in reviewed source'], key=key+'due_status')
            details = st.text_area('Evidence / notes (include report date and geographic scope)', key=key+'due_notes')
            source_url = st.text_input('Exact supporting source URL (https://...)', key=key+'due_url')
            submitted = st.form_submit_button('Save due-diligence entry')
        if submitted:
            if not source_url.startswith('https://') or not details.strip():
                st.error('Provide evidence notes and an HTTPS source URL.')
            else:
                now = datetime.now(timezone.utc)
                entry = dict(key='owner_due_diligence',
                    value_json={'topic': category, 'status': status, 'details': details.strip()},
                    method='manual', confidence='owner_reviewed',
                    source_name='Owner-reviewed source', source_url=source_url.strip(),
                    dataset_vintage='Owner review', retrieved_at=now,
                    note='Owner-reported interpretation; no automated hazard validation.')
                try:
                    _save(session, prop, 'due_diligence_6e', 'due_diligence', [entry], {'topic': category})
                    st.success('Due-diligence entry saved to Neon.')
                except Exception as exc:
                    session.rollback()
                    st.error(f'Unable to save due-diligence entry: {exc}')
        rows = []
        for run in _recent(session, prop, 'due_diligence_6e'):
            for item in session.scalars(select(ResearchItem).where(ResearchItem.run_id == run.id)).all():
                data = item.value_json or {}
                rows.append({'Reviewed': item.retrieved_at, 'Topic': data.get('topic'),
                             'Status': data.get('status'), 'Notes': data.get('details'),
                             'Source': item.source_url, 'Current location': _current(run, prop)})
        if rows:
            st.dataframe(rows, hide_index=True, width='stretch')
