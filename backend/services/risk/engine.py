"""Deterministic route-risk assessment (F8, L-B5, L-B6, L-B7, L-B8). No AI, no randomness, no I/O.

Same route + weather snapshot + times -> same result (AC-B9).
Principle: an uncertain result is preferred over a confidently wrong one. UNKNOWN IS NOT SAFE.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from backend.models.records import (SEVERITY_RANK, WeatherRecord, iso, temporal_status)
from backend.services.weather.base import WeatherSnapshot

RULE_VERSION = "risk-rules-v1"
GEO_COMPLETE_RATIO = 0.999


def assess_route(
    route: Dict[str, Any], matching: Optional[Dict[str, Any]], weather: WeatherSnapshot,
    departure: datetime, now: datetime, *, uncertain_district_ids: Optional[Set[str]] = None,
    max_weather_age_s: int = 6 * 3600,
) -> Dict[str, Any]:
    uncertain_district_ids = uncertain_district_ids or set()
    limitations: List[str] = []
    travel_end = departure + timedelta(seconds=route["duration_s"])
    districts: List[str] = list(matching["district_ids"]) if matching else []

    # ---- geographic coverage (L-A4: only verified intersections) ----
    if matching is None:
        geo_complete = False
        limitations.append("District boundary data is unavailable, so this route could not be mapped to districts.")
    else:
        geo_complete = matching["coverage_ratio"] >= GEO_COMPLETE_RATIO
        if not geo_complete:
            unknown_km = matching["unknown_length_m"] / 1000.0
            pct = 100.0 * (1.0 - matching["coverage_ratio"])
            limitations.append(
                f"{unknown_km:.1f} km ({pct:.0f}%) of this route lies outside the verified Haryana district "
                "boundaries and cannot be assessed.")
        if not districts:
            limitations.append("No verified district was intersected by this route.")
    flagged = [d for d in districts if d in uncertain_district_ids]
    if flagged:
        geo_complete = False
        limitations.append(
            "Part of this route is in a district whose boundary may predate the creation of the Hansi district; "
            "district association there is uncertain.")

    # ---- weather availability ----
    stale = False
    if not weather.usable:
        limitations.append(
            "Weather information is unavailable (" + (weather.errors[0] if weather.errors else weather.status)
            + "). Absence of warnings here does NOT mean absence of risk.")
    else:
        for e in weather.errors:
            limitations.append(e)
        if weather.retrieved_at is not None and (now - weather.retrieved_at).total_seconds() > max_weather_age_s:
            stale = True
            limitations.append("Available weather information may be outdated. Treat this assessment with caution.")

    with_data = [d for d in districts if d in weather.covered_district_ids] if weather.usable else []
    without_data = [d for d in districts if d not in with_data]
    if weather.usable and without_data:
        limitations.append(f"Weather data was not available for {len(without_data)} district(s) on this route.")

    # ---- temporal applicability (L-B5) and highest severity (L-B6) ----
    applicable: List[WeatherRecord] = []
    uncertain_records: List[WeatherRecord] = []
    if weather.usable:
        route_districts = set(districts)
        for rec in weather.records:
            if rec.district_id not in route_districts:
                continue
            ts = temporal_status(rec, departure, travel_end)
            if ts == "applicable":
                if rec.severity in SEVERITY_RANK:
                    applicable.append(rec)
                else:
                    uncertain_records.append(rec)   # severity "unknown": never treated as green
            elif ts == "uncertain":
                uncertain_records.append(rec)
    if uncertain_records:
        limitations.append(f"{len(uncertain_records)} weather record(s) had missing validity or unmapped severity "
                           "and were treated as uncertain, not as current warnings.")

    highest = max((SEVERITY_RANK[r.severity] for r in applicable), default=None)
    warning_rank = highest if highest is not None and highest > 0 else None
    if warning_rank is not None:
        risk = ["green", "yellow", "orange", "red"][warning_rank]
    elif (geo_complete and weather.usable and not stale and districts and not without_data
          and not uncertain_records):
        risk = "green"   # complete coverage AND no applicable warning (L-B6)
    else:
        risk = "unknown"
    if risk in ("yellow", "orange", "red") and (not geo_complete or without_data or stale or uncertain_records):
        limitations.append("The highest observed warning is shown, but coverage is incomplete; other warnings may exist.")

    # ---- coverage status ----
    if not weather.usable or not districts:
        coverage = "unavailable"
    elif geo_complete and not without_data and not stale and not uncertain_records:
        coverage = "complete"
    else:
        coverage = "partial"

    findings = []
    for rec in applicable:
        if rec.severity == "green":
            continue
        findings.append({
            "route_id": route["route_id"], "district_id": rec.district_id, "hazard_type": rec.hazard_type,
            "severity": rec.severity, "record_id": rec.record_id, "evidence_ref": rec.evidence_ref,
            "valid_from": iso(rec.valid_from), "valid_until": iso(rec.valid_until),
            "source_name": rec.source_name, "feed": rec.feed, "rule": "highest_applicable_severity",
        })
    hazards: List[str] = []
    for f in findings:
        if f["hazard_type"] not in hazards:
            hazards.append(f["hazard_type"])
    affected: List[str] = []
    for d in districts:
        if any(f["district_id"] == d for f in findings):
            affected.append(d)
    return {
        "route_id": route["route_id"], "risk_level": risk, "coverage_status": coverage,
        "geographic_coverage_ratio": matching["coverage_ratio"] if matching else None,
        "weather_coverage": {"status": weather.status, "districts_with_data": with_data,
                             "districts_without_data": without_data},
        "districts_traversed": districts, "affected_districts": affected, "hazards": hazards,
        "evidence_refs": [f["evidence_ref"] for f in findings], "findings": findings,
        "limitations": limitations, "assessed_at": iso(now), "rule_version": RULE_VERSION,
        "travel_window": {"start": iso(departure), "end": iso(travel_end)},
    }


def compare_routes(summaries: List[Dict[str, Any]], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """L-B8. Weather-based preference is claimed ONLY when every route has a documented level and the
    levels differ. Otherwise the comparison is geographic only. Never calls a route 'safe'."""
    by_id = {r["route_id"]: r for r in results}
    rows = []
    for s in summaries:
        r = by_id[s["route_id"]]
        rows.append({
            "route_id": s["route_id"], "label": s.get("label"), "distance_m": s["distance_m"],
            "duration_s": s["duration_s"], "risk_level": r["risk_level"], "coverage_status": r["coverage_status"],
            "geographic_coverage_ratio": r["geographic_coverage_ratio"],
            "districts_count": len(r["districts_traversed"]), "affected_districts_count": len(r["affected_districts"]),
        })
    notes: List[str] = []
    fastest = min(rows, key=lambda x: x["duration_s"])["route_id"] if rows else None
    shortest = min(rows, key=lambda x: x["distance_m"])["route_id"] if rows else None
    basis, lowest = "none", []
    if len(rows) < 2:
        notes.append("Only one route is available, so no comparison is possible.")
    else:
        documented = all(r["risk_level"] in SEVERITY_RANK for r in rows)
        ranks = [SEVERITY_RANK.get(r["risk_level"]) for r in rows]
        if documented and min(ranks) != max(ranks):
            basis = "weather"
            lowest = [r["route_id"] for r in rows if SEVERITY_RANK[r["risk_level"]] == min(ranks)]
            notes.append("Routes differ in documented official weather-warning exposure. This is not a guarantee of safety.")
            if any(r["coverage_status"] != "complete" for r in rows):
                notes.append("Some routes have incomplete coverage; other warnings may exist.")
        else:
            basis = "geographic_only"
            if not documented:
                notes.append("At least one route has no documented weather-risk level (weather or boundary coverage "
                             "incomplete), so no route can be called lower-risk. Routes are compared on "
                             "distance, duration and districts crossed only.")
            else:
                notes.append("All routes have the same documented risk level; differences in distance and duration "
                             "are not a safety advantage.")
    return {
        "basis": basis, "lowest_documented_exposure_route_ids": lowest, "fastest_route_id": fastest,
        "shortest_route_id": shortest, "routes": rows, "notes": notes,
        "disclaimer": "Weather warnings indicate potential exposure, not confirmed road closure or disruption.",
    }
