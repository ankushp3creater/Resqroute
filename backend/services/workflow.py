"""Orchestration of the primary journey (spec 6.1):
validate -> geocode -> routes -> district matching -> weather -> risk -> (AI) -> comparison.
Deterministic steps never call the LLM. Every step is recorded in the trace (F12, L-B13).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.errors import ApiError
from backend.models.records import IST, clean_text, display_state, iso, utcnow
from backend.models.validation import validate_assessment_request, validate_route_request
from backend.services.ai.agent import run_agent
from backend.services.ai.gemini_client import AiError, AiNotConfigured
from backend.services.geography.matcher import haversine_m, match_route
from backend.services.risk.engine import assess_route, compare_routes
from backend.services.routing.base import new_id

STANDING_LIMITATION = ("Weather warnings indicate potential exposure, not confirmed road closure, accident or disruption. "
                       "ResQRoute does not guarantee that any route is safe.")


def event(trace: List[Dict[str, Any]], etype: str, message: str, **data: Any) -> None:
    trace.append({"ts": iso(utcnow()), "type": etype, "message": message, "data": data})


# ----------------------------------------------------------------------------- routes
def _route_payload(state, route: Dict[str, Any]) -> Dict[str, Any]:
    m = route.get("matching")
    names = state.dataset.names if state.dataset else {}
    districts = []
    if m:
        lengths: Dict[str, float] = {}
        for s in m["sections"]:
            if s["district_id"]:
                lengths[s["district_id"]] = lengths.get(s["district_id"], 0.0) + s["length_m"]
        for did in m["district_ids"]:
            districts.append({"district_id": did, "district_name": names.get(did, did), "length_m": lengths.get(did, 0.0),
                              "boundary_uncertain": did in state.dataset.uncertain_ids})
    return {
        "route_id": route["route_id"], "route_request_id": route["route_request_id"], "label": route["label"],
        "distance_m": route["distance_m"], "duration_s": route["duration_s"], "geometry": route["geometry"],
        "provider": route["provider"], "retrieved_at": route["retrieved_at"], "provider_notes": route["provider_notes"],
        "geography_status": "available" if m else "unavailable",
        "coverage_ratio": m["coverage_ratio"] if m else None,
        "unknown_length_m": m["unknown_length_m"] if m else None,
        "district_ids": [d["district_id"] for d in districts], "districts": districts,
        "sections": m["sections"] if m else [],
    }


def create_route_request(state, payload: Any) -> Dict[str, Any]:
    now = utcnow()
    req = validate_route_request(payload, now, state.settings.past_departure_grace_seconds)
    trace: List[Dict[str, Any]] = []
    event(trace, "processing", "Input validated", departure_time=iso(req["departure_time"]))
    o = state.geocoder.resolve(req["origin"], "origin")
    d = state.geocoder.resolve(req["destination"], "destination")
    event(trace, "source", "Locations resolved", origin_source=o["source"], destination_source=d["source"],
          provider=state.geocoder.name if "nominatim" in (o["source"], d["source"]) else "user coordinates")
    if haversine_m(o["longitude"], o["latitude"], d["longitude"], d["latitude"]) < 50:
        m = "Origin and destination must be different."
        raise ApiError(422, "SAME_ORIGIN_DESTINATION", m, {"fields": {"destination": m}})

    rr_id = new_id("rr")
    provider_routes = state.router.get_routes(o, d, req["departure_time"])
    event(trace, "source", "Routing provider responded", provider=state.router.name, route_count=len(provider_routes))
    base = {"route_request_id": rr_id, "origin": {"label": o["label"], "source": o["source"]},
            "destination": {"label": d["label"], "source": d["source"]},
            "departure_time": iso(req["departure_time"])}
    if not provider_routes:   # E5 - no fabricated routes, no assessment
        return {**base, "routes": [], "message": "No route was found between these locations. Try different location details.",
                "geography": _geo_status(state)}

    for i, r in enumerate(provider_routes):
        r["route_request_id"] = rr_id
        r["label"] = f"Route {i + 1}"
        if state.dataset is not None:
            coords = [(x, y) for x, y in r["geometry"]["coordinates"]]
            r["matching"] = match_route(state.dataset.index, coords)
            event(trace, "processing", f"{r['label']}: matched to districts",
                  districts=len(r["matching"]["district_ids"]), coverage_ratio=round(r["matching"]["coverage_ratio"], 4))
        else:
            r["matching"] = None
            event(trace, "error", f"{r['label']}: district matching skipped", code="GEOGRAPHY_UNAVAILABLE")
    state.store.put_route_request({"route_request_id": rr_id, "origin_label": o["label"], "destination_label": d["label"],
                                   "departure_time": req["departure_time"], "created_at": iso(now),
                                   "route_ids": [r["route_id"] for r in provider_routes], "trace": trace}, provider_routes)
    return {**base, "routes": [_route_payload(state, r) for r in provider_routes], "geography": _geo_status(state),
            "message": None}


def _geo_status(state) -> Dict[str, Any]:
    if state.dataset is None:
        return {"status": "unavailable", "detail": state.dataset_error}
    md = state.dataset.metadata
    return {"status": "partially_available" if md["coverage_status"] == "partial" else "available",
            "coverage_status": md["coverage_status"], "available_count": md["available_count"],
            "expected_count": md["expected_count"], "warning": md["warning"]}


def get_route(state, route_id: str) -> Dict[str, Any]:
    return _route_payload(state, state.store.get_route(route_id))


# ----------------------------------------------------------------------------- assessments
def create_assessment(state, payload: Any) -> Dict[str, Any]:
    now = utcnow()
    req = validate_assessment_request(payload, now, state.settings.past_departure_grace_seconds)
    routes = [state.store.get_route(rid) for rid in req["route_ids"]]   # 404 if unknown
    if len({r["route_request_id"] for r in routes}) != 1:
        m = "All route_ids must belong to the same route request."
        raise ApiError(422, "INVALID_ROUTE_IDS", m, {"fields": {"route_ids": m}})
    rr = state.store.get_route_request(routes[0]["route_request_id"]) or {"trace": []}
    aid = new_id("as")
    trace: List[Dict[str, Any]] = list(rr["trace"])
    event(trace, "state", "queued", state="queued", assessment_id=aid)
    try:
        event(trace, "state", "processing", state="processing")
        departure: datetime = req["departure_time"]
        snapshot = state.weather.get_snapshot()
        event(trace, "source", f"Weather source '{snapshot.source_name}': {snapshot.status}", source=snapshot.source_name,
              status=snapshot.status, retrieved_at=iso(snapshot.retrieved_at), record_count=len(snapshot.records),
              errors=snapshot.errors)
        uncertain = state.dataset.uncertain_ids if state.dataset else set()
        results = []
        for r in routes:
            res = assess_route(r, r.get("matching"), snapshot, departure, now, uncertain_district_ids=uncertain,
                               max_weather_age_s=state.settings.weather_max_age_seconds)
            results.append(res)
            event(trace, "processing", f"{r['label']}: risk={res['risk_level']}, coverage={res['coverage_status']}",
                  route_id=r["route_id"], rule_version=res["rule_version"], findings=len(res["findings"]))
        summaries = []
        for r in routes:
            p = _route_payload(state, r)
            summaries.append({
                "route_id": p["route_id"], "label": p["label"], "evidence_ref": f"route:{p['route_id']}",
                "distance_m": p["distance_m"], "duration_s": p["duration_s"], "provider": p["provider"],
                "coverage_ratio": p["coverage_ratio"], "unknown_length_m": p["unknown_length_m"],
                "districts": p["districts"], "provider_notes": p["provider_notes"]})
        comparison = compare_routes(summaries, results)
        event(trace, "processing", f"Routes compared (basis: {comparison['basis']})", basis=comparison["basis"])

        # limitations + evidence index (every cited ref must exist here - I14)
        limitation_index: List[Dict[str, str]] = []
        global_lims = [STANDING_LIMITATION]
        if state.dataset and state.dataset.metadata["coverage_status"] == "partial":
            global_lims.append("District boundary dataset covers 22 of 23 Haryana districts (Hansi missing); "
                               "it does not represent fully verified current boundaries.")
        for e in snapshot.errors:
            global_lims.append(e)
        for n in routes[0].get("provider_notes", []):
            global_lims.append(n)
        for t in global_lims:
            limitation_index.append({"evidence_ref": f"lim:{len(limitation_index)}", "scope": "assessment", "text": t})
        for res in results:
            refs = []
            for t in res["limitations"]:
                ref = f"lim:{len(limitation_index)}"
                limitation_index.append({"evidence_ref": ref, "scope": res["route_id"], "text": t})
                refs.append(ref)
            res["limitation_refs"] = refs
        evidence_index: Dict[str, Dict[str, Any]] = {}
        for s in summaries:
            evidence_index[s["evidence_ref"]] = {"type": "route", "label": f"{s['label']} geographic facts", "route_id": s["route_id"]}
        for li in limitation_index:
            evidence_index[li["evidence_ref"]] = {"type": "limitation", "label": li["text"], "scope": li["scope"]}
        used = {f["record_id"] for res in results for f in res["findings"]}
        for rec in snapshot.records:
            if rec.record_id in used:
                evidence_index[rec.evidence_ref] = {"type": "weather_record", "label": f"{rec.hazard_type} {rec.severity} "
                                                    f"({rec.district_id})", "record": rec.to_dict(now)}
        status = "completed" if all(x["coverage_status"] == "complete" for x in results) else "partial"
        event(trace, "state", status, state=status)
        assessment = {
            "assessment_id": aid, "status": status, "deterministic_status": status,
            "route_request_id": routes[0]["route_request_id"], "departure_time": iso(departure),
            "created_at": iso(now), "completed_at": iso(utcnow()),
            "origin_label": rr.get("origin_label"), "destination_label": rr.get("destination_label"),
            "results": results, "comparison": comparison, "route_summaries": summaries,
            "weather": snapshot.summary(), "limitation_index": limitation_index, "evidence_index": evidence_index,
            "ai": {"status": "not_requested", "analysis": None, "error": None}, "trace": trace,
        }
        state.store.put_assessment(assessment)
        return assessment
    except ApiError:
        raise
    except Exception:   # unexpected: mark failed, never leak internals
        event(trace, "state", "failed", state="failed")
        raise ApiError(500, "ASSESSMENT_FAILED", "The assessment could not be completed. Please try again.",
                       {"assessment_id": aid})


def get_assessment(state, aid: str) -> Dict[str, Any]:
    return state.store.get_assessment(aid)


# ----------------------------------------------------------------------------- AI
def run_ai_analysis(state, aid: str) -> Dict[str, Any]:
    a = state.store.get_assessment(aid)
    lock = state.store.ai_lock(aid)
    if not lock.acquire(blocking=False):
        raise ApiError(409, "AI_ANALYSIS_IN_PROGRESS", "AI analysis is already running for this assessment.", {})
    try:
        if not state.gemini.configured:
            a["ai"] = {"status": "not_configured", "analysis": None, "error": {"code": "AI_NOT_CONFIGURED"}}
            event(a["trace"], "error", "AI analysis not configured", code="AI_NOT_CONFIGURED")
            raise ApiError(503, "AI_NOT_CONFIGURED",
                           "AI analysis is not configured. Deterministic route-risk results are still available.", {})
        a["status"] = "ai_pending"
        a["ai"] = {"status": "pending", "analysis": None, "error": None}
        event(a["trace"], "state", "ai_pending", state="ai_pending")
        try:
            analysis = run_agent(a, state.gemini, max_steps=state.settings.agent_max_steps,
                                 deadline_s=state.settings.ai_timeout_seconds * 2)
        except AiError as exc:
            a["status"] = "ai_failed"
            a["ai"] = {"status": "failed", "analysis": None,
                       "error": {"code": exc.code, "message": exc.message}, "tool_trace": exc.tool_trace}
            event(a["trace"], "error", f"AI analysis failed: {exc.code}", code=exc.code)
            raise ApiError(exc.http_status, exc.code, exc.message,
                           {"assessment_id": aid, "tool_trace": exc.tool_trace})
        a["status"] = a["deterministic_status"]
        generated = iso(utcnow())
        resp = {
            "assessment_id": aid, "status": "completed", "generated_at": generated, "model": state.gemini.model,
            "ai_generated": True, **analysis,
            "reason": analysis["recommendation_reason"],   # alias used by spec section 7
        }
        a["ai"] = {"status": "completed", "analysis": resp, "error": None}
        event(a["trace"], "ai", "AI analysis completed and validated", steps_used=analysis["steps_used"],
              retries_used=analysis["retries_used"])
        return resp
    finally:
        lock.release()


# ----------------------------------------------------------------------------- weather endpoints
def district_weather(state, district_id: str, date: Optional[str]) -> Dict[str, Any]:
    if state.dataset is None:
        raise ApiError(503, "GEOGRAPHY_UNAVAILABLE", "District boundary data is currently unavailable.", {})
    if district_id not in state.dataset.by_id:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "This district could not be found.", {})
    day_start = day_end = None
    if date is not None:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError:
            raise ApiError(422, "INVALID_DATE", "date must be in YYYY-MM-DD format.", {"fields": {"date": "Use YYYY-MM-DD."}})
        day_start, day_end = d, d + timedelta(days=1)
    snap = state.weather.get_snapshot()
    now = utcnow()
    recs = [r for r in snap.records if r.district_id == district_id]
    if day_start is not None:
        recs = [r for r in recs if r.valid_from is None or r.valid_until is None or (r.valid_from < day_end and r.valid_until >= day_start)]
    items = [r.to_dict(now) for r in recs]
    if not snap.usable:
        summary, message = "unavailable", "Weather information is unavailable: " + (snap.errors[0] if snap.errors else snap.status)
    elif district_id not in snap.covered_district_ids:
        summary, message = "unavailable", "The weather source did not return data for this district."
    elif not items:
        summary, message = "no_warning_records", "No matching IMD warning was found for this district. This alone is not proof of zero risk."
    elif all(i["display_state"] == "expired" for i in items):
        summary, message = "expired_only", "Only expired records are available; they are not current warnings."
    else:
        summary, message = "records_present", "Records are listed below with their source and validity."
    return {"district_id": district_id, "district_name": state.dataset.names[district_id], "date": date,
            "source_name": snap.source_name, "source_status": snap.status, "retrieved_at": iso(snap.retrieved_at),
            "summary": summary, "message": message, "records": items, "errors": snap.errors}


def refresh_data(state) -> Dict[str, Any]:
    now = utcnow()
    sources = []
    snap = state.weather.get_snapshot(force=True)
    sources.append({"name": snap.source_name, "kind": "weather", "status": snap.status,
                    "retrieved_at": iso(snap.retrieved_at), "record_count": len(snap.records),
                    "errors": snap.errors, "feeds": snap.feeds})
    state.reload_dataset()
    if state.dataset is None:
        sources.append({"name": "Haryana district boundaries", "kind": "geography", "status": "unavailable",
                        "retrieved_at": None, "errors": [state.dataset_error]})
    else:
        md = state.dataset.metadata
        sources.append({"name": "Haryana district boundaries", "kind": "geography",
                        "status": "partially_available" if md["coverage_status"] == "partial" else "connected",
                        "retrieved_at": md["retrieved_at"], "available_count": md["available_count"], "errors": []})
    return {"refreshed_at": iso(now), "sources": sources,
            "note": "Existing assessments are not modified; create a new assessment to use refreshed data."}
