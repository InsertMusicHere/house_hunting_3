"""Read-only Phase 6f aggregation. No new calculations or external requests."""
from __future__ import annotations
from collections import defaultdict
from sqlalchemy import select
from database.models import Property, FinancialDetails, NeighbourhoodResearch, ResearchItem
from services.financial_rules import load_rules
from services.scenarios import staleness

POLICE = ("tps_neighbourhood_6d", "prp_region_6d1")
MANUAL = ("maternity", "school_verification", "due_diligence")

def _location_matches(run, prop):
    params = run.params or {}
    loc = params.get("location")
    if loc is None:
        return False
    if prop.location_verified_at is None or prop.latitude is None or prop.longitude is None:
        return False
    try:
        if abs(float(loc[0])-float(prop.latitude)) > 0.0000002 or abs(float(loc[1])-float(prop.longitude)) > 0.0000002:
            return False
    except (TypeError, ValueError, IndexError):
        return False
    verified = params.get("location_verified_at")
    return verified is None or verified == str(prop.location_verified_at)

def _number(item):
    return float(item.value_num) if item is not None and item.value_num is not None else None

def _snapshot(run, items):
    return {"run_at": run.run_at.isoformat(), "provider": (run.params or {}).get("provider"),
            "source": next((i.source_name for i in items if i.source_name), None),
            "source_url": next((i.source_url for i in items if i.source_url), None)}

def build_dossiers(session, property_ids=None):
    """Aggregate persisted evidence for all/selected properties using four bulk queries."""
    properties = list(session.scalars(select(Property).order_by(Property.city, Property.address)))
    if property_ids is not None:
        wanted = {str(x) for x in property_ids}
        properties = [p for p in properties if str(p.id) in wanted]
    if not properties:
        return []
    ids = [p.id for p in properties]
    scenarios = list(session.scalars(select(FinancialDetails).where(FinancialDetails.property_id.in_(ids))))
    runs = list(session.scalars(select(NeighbourhoodResearch).where(
        NeighbourhoodResearch.property_id.in_(ids), NeighbourhoodResearch.status == "success"
    ).order_by(NeighbourhoodResearch.run_at.desc())))
    items = list(session.scalars(select(ResearchItem).where(ResearchItem.run_id.in_([r.id for r in runs])))) if runs else []
    by_scenario, by_run, by_property = defaultdict(list), defaultdict(list), defaultdict(list)
    for s in scenarios: by_scenario[s.property_id].append(s)
    for i in items: by_run[i.run_id].append(i)
    for r in runs: by_property[r.property_id].append(r)
    rules = load_rules()
    dossiers = []
    for prop in properties:
        scenario = next((s for s in by_scenario[prop.id] if s.is_primary), None)
        calc = scenario.calculated if scenario else None
        valid_calc = bool(calc and calc.get("status") == "ok")
        stale_reasons = staleness(scenario, prop, rules) if scenario and valid_calc else []
        finance = {
            "scenario": scenario.scenario_name if scenario else None,
            "status": ("missing" if scenario is None else "not_calculated" if not calc
                       else calc.get("status", "unknown")),
            "out_of_date": bool(stale_reasons),
            "out_of_date_reasons": stale_reasons,
            "calculated_at": scenario.calculated_at.isoformat() if scenario and scenario.calculated_at else None,
            "monthly_outgoing": calc.get("monthly", {}).get("total") if valid_calc else None,
            "cash_required": calc.get("cash", {}).get("total_cash_required") if valid_calc else None,
            "mortgage_payment": calc.get("mortgage", {}).get("monthly_payment") if valid_calc else None,
            "purchase_price": calc.get("mortgage", {}).get("purchase_price") if valid_calc else None,
        }
        selected = {}
        notes = {k: [] for k in MANUAL}
        for run in by_property[prop.id]:
            if not _location_matches(run, prop):
                continue
            provider = (run.params or {}).get("provider")
            entries = by_run[run.id]
            # A research run's stored provider is authoritative for the snapshot.
            if provider and provider not in selected:
                selected[provider] = (run, entries)
            for item in entries:
                if item.category in notes and item.key in ("owner_verified_note", "owner_due_diligence"):
                    notes[item.category].append({
                        "details": item.value_json or {}, "source_url": item.source_url,
                        "recorded_at": item.retrieved_at.isoformat() if item.retrieved_at else None})
        # Convenience runs may use another provider label: find the latest proximity_score.
        convenience = None
        for run in by_property[prop.id]:
            if not _location_matches(run, prop):
                continue
            score = next((i for i in by_run[run.id] if i.key == "proximity_score"), None)
            if score:
                convenience = {**_snapshot(run, by_run[run.id]), "score": _number(score)}
                break
        essentials = None
        for run in by_property[prop.id]:
            if not _location_matches(run, prop):
                continue
            provider = (run.params or {}).get("provider", "")
            if ("essential" in provider.lower() or any(
                i.category in ("healthcare", "schools", "transit", "essential_services", "essentials")
                for i in by_run[run.id]
            )):
                essentials = {"metadata": _snapshot(run, by_run[run.id]),
                              "metrics": {i.key: _number(i) if i.value_num is not None else i.value_json or i.value_text
                                          for i in by_run[run.id]}}
                break
        transit = None
        for run in by_property[prop.id]:
            if _location_matches(run, prop) and (run.params or {}).get("provider") == "uploaded_gtfs_6c":
                item = next((i for i in by_run[run.id] if i.key == "scheduled_stop_departures"), None)
                if item:
                    transit = {**_snapshot(run, by_run[run.id]), "departures": _number(item),
                               "schedule_date": (run.params or {}).get("schedule_date"),
                               "window": (run.params or {}).get("window")}
                    break
        police = None
        for provider in POLICE:
            if provider in selected:
                run, entries = selected[provider]
                item = next((i for i in entries if i.key == "neighbourhood_police_summary"), None)
                if item:
                    data = item.value_json or {}
                    police = {**_snapshot(run, entries), "area": data.get("neighbourhood"),
                              "year": data.get("year"), "counts": data.get("counts", {}),
                              "score": data.get("relative_indicator_score") if provider == POLICE[0] else None,
                              "scope": "Toronto neighbourhood" if provider == POLICE[0] else "Peel-wide",
                              "note": data.get("note")}
                    break
        dossiers.append({
            "id": str(prop.id), "address": prop.address, "unit": prop.unit,
            "city": prop.city, "postal_code": prop.postal_code,
            "listing_url": prop.listing_url, "asking_price": str(prop.asking_price) if prop.asking_price is not None else None,
            "property_type": prop.property_type, "bedrooms": prop.bedrooms, "bathrooms": prop.bathrooms,
            "size_min": prop.interior_size_min, "size_max": prop.interior_size_max,
            "location_verified": prop.location_verified_at is not None,
            "financial": finance, "convenience": convenience, "essentials": essentials,
            "transit": transit, "police": police, "notes": notes,
        })
    return dossiers
