"""Optional verified ward-level aggregate importer for existing Phase 6d Streamlit tab."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import streamlit as st
from services.research.peel_ward import CATEGORIES, WardDataError, parse_ward_csv, evaluate_ward

WARD_MAP = 'https://www.mississauga.ca/council/city-council-members/wards/'

def render_ward_import(session, prop, key, save_run):
    city = (prop.city or '').strip().lower()
    if city not in ('mississauga', 'brampton'):
        return
    with st.expander('Ward-level police aggregates — verified CSV import', expanded=False):
        st.warning('Import aggregated official ward counts only. Never upload individual incident records or addresses. '
                   'This tool does not verify the CSV against Peel Police automatically.')
        st.markdown(f'[Check Mississauga ward boundaries]({WARD_MAP})')
        st.caption('Required CSV columns: municipality,ward,year,population,source_url,' + ','.join(CATEGORIES))
        file = st.file_uploader('Official ward-level aggregate CSV', type=['csv'], key=key+'ward_csv')
        ward = st.number_input('Property ward number (verify with municipal ward map)', min_value=1, max_value=50,
                               value=1, step=1, key=key+'ward_number')
        verified = st.checkbox('I verified the ward for this property and the CSV source, population year and crime definitions',
                               key=key+'ward_verified')
        if file is not None:
            try:
                rows = parse_ward_csv(file.getvalue())
                years = sorted({r['year'] for r in rows if r['municipality'] == city}, reverse=True)
                if not years:
                    st.warning('No data for this municipality')
                    return
                year = st.selectbox('Reporting year', years, key=key+'ward_year')
                result = evaluate_ward(rows, city, int(ward), year)
                st.metric('Within-municipality relative police-data index (not safety)',
                          f"{result['relative_indicator_score']:.1f}/100" if result['relative_indicator_score'] is not None else 'Unavailable')
                st.caption(f"{result['peer_count']} comparable wards · {result['combined_selected_rate_per_100k']:.1f} summed selected rates per 100,000")
                st.dataframe([{'Category': c, 'Count': result['counts'][c],
                               'Rate per 100,000': result['rates_per_100k'][c]} for c in CATEGORIES],
                             hide_index=True, width='stretch')
                st.caption(result['note'])
                if st.button('Save verified ward aggregate snapshot', disabled=not verified,
                             key=key+'ward_save'):
                    now = datetime.now(timezone.utc)
                    common = dict(method='owner_imported_aggregate', confidence='owner_verified',
                                  source_name='Owner-verified Peel ward aggregate dataset',
                                  source_url=result['source_url'], dataset_vintage=f"Year {year}; population vintage must be verified",
                                  retrieved_at=now, attribution='See original dataset licence', note=result['note'])
                    entries = [dict(key='neighbourhood_police_summary', value_json=result, **common)]
                    if result['relative_indicator_score'] is not None:
                        entries.append(dict(key='relative_indicator_score',
                                            value_num=Decimal(str(result['relative_indicator_score'])),
                                            unit='0-100 within-municipality relative index', **common))
                    try:
                        save_run(session, prop, 'prp_ward_6d2', 'police_statistics', entries,
                                 {'year': year, 'ward': int(ward), 'municipality': city, 'geographic_scope': 'municipal ward'})
                        st.success('Ward aggregate snapshot saved to Neon.')
                    except Exception as exc:
                        session.rollback()
                        st.error(f'Could not save ward snapshot: {exc}')
            except WardDataError as exc:
                st.error(str(exc))
