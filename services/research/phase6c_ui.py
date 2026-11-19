"""Phase 6c Streamlit section. Import and invoke only after login and property selection."""
from __future__ import annotations
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
import streamlit as st
from sqlalchemy import select
from database.models import NeighbourhoodResearch, ResearchItem
from services.research.cache import key_for, read, write
from services.research.essentials import PRIMARY, ATTRIBUTION, Place, fetch, parse, summary
from services.research.essential_runs import save_run, latest_run
from services.research.gtfs import analyze_zip

OFFICIAL = {
    'Ontario hospital directory': 'https://www.ontario.ca/page/hospitals',
    'BORN Ontario': 'https://www.bornontario.ca/',
    'Provincial Council for Maternal and Child Health': 'https://www.pcmch.on.ca/',
    'Ontario school information': 'https://www.ontario.ca/page/find-your-school',
    'EQAO school results': 'https://www.eqao.com/',
    'TTC open data': 'https://open.toronto.ca/',
    'Metrolinx open data': 'https://www.metrolinx.com/en/about-us/open-data',
}


def _save_manual(session, prop, category, name, details, url):
    if not name.strip() or not url.startswith('https://'):
        raise ValueError('Enter a name and an https:// official source URL')
    now = datetime.now(timezone.utc)
    run = NeighbourhoodResearch(property_id=prop.id, run_at=now, radius_m=0, status='success',
        params={'provider': 'manual_verification_6c', 'location': [prop.latitude, prop.longitude]}, provider_status={'manual': 'recorded'})
    session.add(run); session.flush()
    session.add(ResearchItem(run_id=run.id, category=category, key='owner_verified_note',
        value_json={'name': name.strip(), 'details': details.strip(), 'verified_on': date.today().isoformat()},
        method='manual', confidence='owner_reviewed', source_name='Owner-reviewed official source',
        source_url=url, dataset_vintage='Owner checked on ' + date.today().isoformat(),
        retrieved_at=now, note='Owner-entered verification; not independently validated by the app'))
    session.commit()


def _manual_history(session, prop, category):
    rows = session.scalars(select(ResearchItem).join(NeighbourhoodResearch, ResearchItem.run_id == NeighbourhoodResearch.id).where(
        NeighbourhoodResearch.property_id == prop.id, ResearchItem.category == category,
        ResearchItem.key == 'owner_verified_note').order_by(ResearchItem.retrieved_at.desc()).limit(30)).all()
    if rows:
        st.dataframe([{'Name': (r.value_json or {}).get('name'), 'Details': (r.value_json or {}).get('details'),
            'Verified': (r.value_json or {}).get('verified_on'), 'Source': r.source_url} for r in rows], hide_index=True, width='stretch')


def render(session, prop, key):
    st.divider()
    st.subheader('Healthcare, schools and transit — Phase 6c')
    st.caption('Mapped features are discovery leads only. Straight-line distances are not travel times. No hospital safety ranking, school designation or service frequency is inferred from OSM.')
    if prop.latitude is None or prop.longitude is None or prop.location_verified_at is None:
        st.info('Verify the property location above before researching essential services.')
        return
    lat, lon = float(prop.latitude), float(prop.longitude)
    tab_health, tab_school, tab_transit = st.tabs(['Hospitals & maternity', 'Primary & secondary schools', 'Transit & GTFS'])
    with st.expander('Discover mapped essential services (shared by all three tabs)', expanded=True):
        radius = st.selectbox('Essential services radius', [1200, 2000, 3000], index=2, key=key+'ess_radius')
        force = st.checkbox('Refresh OSM instead of using 7-day cache', key=key+'ess_force')
        if st.button('Discover hospitals, schools and transit', key=key+'ess_fetch'):
            try:
                ck = key_for('essentials_6c_v1', {'lat': round(lat, 7), 'lon': round(lon, 7), 'radius': radius})
                cached = None if force else read(session, ck)
                if cached is None:
                    with st.spinner('Querying OpenStreetMap...'):
                        data = fetch(lat, lon, radius)
                        cached = [p.public_dict() for p in parse(data, lat, lon, radius)]
                    write(session, ck, 'essentials_6c_v1', cached, ttl_days=7)
                    session.commit()
                places = [Place(**p) for p in cached]
                run = save_run(session, prop, summary(places), radius_m=radius, source_url=PRIMARY)
                session.commit()
                st.session_state[key+'ess_result'] = {'places': cached, 'location': (lat, lon, str(prop.location_verified_at)), 'radius': radius, 'run': str(run.id)}
                st.success('Essential services snapshot saved to Neon.')
            except Exception as exc:
                session.rollback()
                st.error(f'Essential services lookup failed: {exc}')
        latest = latest_run(session, prop.id)
        if latest:
            st.caption(f'Latest persisted essentials snapshot: {latest.run_at}. Historical counts remain in Neon; re-run discovery to display individual places.')
    state = st.session_state.get(key+'ess_result')
    places = [Place(**p) for p in state['places']] if state and state['location'] == (lat, lon, str(prop.location_verified_at)) else []
    if places:
        st.caption(f'{len(places)} mapped category matches. Some physical sites have multiple OSM tags. {ATTRIBUTION}')
    with tab_health:
        st.markdown('**Nearby mapped healthcare locations**')
        health = [p for p in places if p.category in ('hospital', 'clinic', 'doctors', 'urgent_care')]
        st.dataframe([{'Type': p.category, 'Name': p.name or 'Unnamed', 'Distance (m)': round(p.distance_m), 'OSM': f'{p.osm_type}/{p.osm_id}'} for p in health], hide_index=True, width='stretch') if health else st.info('Run discovery to see healthcare locations; no results does not prove absence.')
        st.warning('Maternity/labour and delivery, emergency availability, NICU level and patient safety are NOT verified by OSM proximity. Confirm directly with the hospital and authoritative Ontario sources. A higher NICU level is not a safety ranking.')
        for label, url in list(OFFICIAL.items())[:3]: st.markdown(f'[{label}]({url})')
        with st.form(key+'maternity_form'):
            name = st.text_input('Hospital / maternity program name')
            details = st.text_area('Verified details (labour & delivery, NICU level, service date, notes)')
            url = st.text_input('Official source URL (https://...)')
            if st.form_submit_button('Save owner-verified maternity research'):
                try: _save_manual(session, prop, 'maternity', name, details, url); st.success('Maternity note saved.')
                except ValueError as exc: st.error(str(exc))
        _manual_history(session, prop, 'maternity')
    with tab_school:
        st.markdown('**Nearby mapped educational locations**')
        schools = [p for p in places if p.category in ('school', 'kindergarten', 'college')]
        st.dataframe([{'OSM type': p.category, 'Name': p.name or 'Unnamed', 'Distance (m)': round(p.distance_m), 'OSM': f'{p.osm_type}/{p.osm_id}'} for p in schools], hide_index=True, width='stretch') if schools else st.info('Run discovery to see schools.')
        st.warning('OSM school tags do not reliably distinguish primary from secondary grades. The nearest school is not necessarily the designated school. Verify attendance boundaries, eligibility, board, grade range and EQAO results independently.')
        for label, url in list(OFFICIAL.items())[3:5]: st.markdown(f'[{label}]({url})')
        with st.form(key+'school_form'):
            name = st.text_input('School name')
            details = st.text_area('Board, primary/secondary grades, designated status, EQAO year and notes')
            url = st.text_input('Official board locator or school source URL (https://...)')
            if st.form_submit_button('Save owner-verified school research'):
                try: _save_manual(session, prop, 'school_verification', name, details, url); st.success('School note saved.')
                except ValueError as exc: st.error(str(exc))
        _manual_history(session, prop, 'school_verification')
    with tab_transit:
        stops = [p for p in places if p.category in ('bus_stop', 'tram_stop', 'station', 'subway_entrance')]
        st.markdown('**Nearby mapped transit access**')
        st.dataframe([{'Type': p.category, 'Name': p.name or 'Unnamed', 'Distance (m)': round(p.distance_m)} for p in stops], hide_index=True, width='stretch') if stops else st.info('Run discovery to see mapped stops.')
        st.caption('Mapped stops alone do not establish service quality or operating hours. Upload a current official GTFS static feed to count scheduled departures.')
        for label, url in list(OFFICIAL.items())[5:]: st.markdown(f'[{label}]({url})')
        uploaded = st.file_uploader('Official GTFS static feed (.zip, maximum 30 MB)', type='zip', key=key+'gtfs_file')
        day = st.date_input('Schedule date (local to agency)', value=date.today(), key=key+'gtfs_date')
        a, b = st.columns(2)
        start = a.number_input('Window start hour', min_value=0, max_value=23, value=7, key=key+'gtfs_start')
        end = b.number_input('Window end hour (exclusive)', min_value=1, max_value=24, value=9, key=key+'gtfs_end')
        if st.button('Analyze uploaded GTFS schedule', disabled=uploaded is None, key=key+'gtfs_analyze'):
            try:
                result = analyze_zip(uploaded.getvalue(), lat, lon, day, radius_m=800, start_hour=start, end_hour=end)
                st.session_state[key+'gtfs_result'] = result
                now = datetime.now(timezone.utc)
                run = NeighbourhoodResearch(property_id=prop.id, run_at=now, radius_m=800, status='success',
                    params={'provider': 'uploaded_gtfs_6c', 'location': [lat, lon], 'schedule_date': day.isoformat(), 'window': [start, end], 'agency': result['agency'], 'feed_version': result['feed_version']}, provider_status={'uploaded_gtfs': 'parsed'})
                session.add(run); session.flush()
                session.add(ResearchItem(run_id=run.id, category='transit_schedule', key='scheduled_stop_departures',
                    value_num=Decimal(result['total_stop_departures']), unit='stop departures', method='measured', confidence='medium',
                    source_name='User-uploaded GTFS static feed: '+result['agency'], source_url=None,
                    dataset_vintage=result['feed_version'] or result['feed_start_date'] or 'Unknown feed vintage', retrieved_at=now,
                    note=result['note'] + ' Original ZIP not retained.'))
                session.commit()
                st.success('Scheduled departures analyzed and aggregate saved to Neon.')
            except Exception as exc:
                session.rollback(); st.error(f'GTFS analysis failed: {exc}')
        result = st.session_state.get(key+'gtfs_result')
        if result:
            st.metric('Scheduled stop departures in selected window', result['total_stop_departures'])
            st.caption(result['note'])
            if result['frequency_based_service_not_counted']: st.warning('Feed includes frequencies.txt; frequency-based trips were not counted. Total may be incomplete.')
            st.dataframe(result['stops'][:100], hide_index=True, width='stretch')
