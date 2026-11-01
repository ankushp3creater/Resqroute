"""Unit tests for deterministic logic. Runs under pytest or backend/tests/run_lite.py (stdlib only)."""
from datetime import timedelta
from pathlib import Path

from backend.errors import ApiError
from backend.models.records import display_state
from backend.models.validation import validate_route_request, validate_assessment_request
from backend.services.geography.loader import load_dataset
from backend.services.geography.matcher import match_route
from backend.services.risk.engine import assess_route, compare_routes
from backend.services.routing.base import parse_and_validate_route_geometry
from backend.services.store import IdempotencyStore
from backend.tests.helpers import NOW, raises, snapshot, make_record

DATA = Path(__file__).resolve().parents[1] / "data" / "haryana_district_boundaries.geojson"
DS = load_dataset(DATA)
ID = {n: i for i, n in DS.names.items()}
DEP = NOW + timedelta(hours=1)


def _req(**over):
    base = {"origin": {"text": "Ambala"}, "destination": {"text": "Karnal"}, "departure_time": DEP.isoformat()}
    base.update(over)
    return base


# ---- spec-named tests ----
def test_validate_route_inputs():
    ok = validate_route_request(_req(), NOW)
    assert ok["origin"]["text"] == "Ambala"
    with raises(ApiError, "INVALID_ROUTE_INPUT"):
        validate_route_request(_req(origin={"text": "  "}), NOW)
    with raises(ApiError, "INVALID_ROUTE_INPUT"):
        validate_route_request(_req(origin={"latitude": 95, "longitude": 10}), NOW)
    with raises(ApiError, "INVALID_ROUTE_INPUT"):
        validate_route_request(_req(origin={"text": "x" * 201}), NOW)
    with raises(ApiError, "INVALID_DEPARTURE_TIME"):
        validate_route_request(_req(departure_time="2026-11-02T08:00:00"), NOW)       # no offset
    with raises(ApiError, "INVALID_DEPARTURE_TIME"):
        validate_route_request(_req(departure_time=(NOW - timedelta(hours=2)).isoformat()), NOW)  # past
    with raises(ApiError, "INVALID_DEPARTURE_TIME"):
        validate_route_request(_req(departure_time="tomorrow"), NOW)
    with raises(ApiError, "INVALID_ROUTE_IDS"):
        validate_assessment_request({"route_ids": [], "departure_time": DEP.isoformat()}, NOW)


def test_reject_identical_origin_destination():
    with raises(ApiError, "SAME_ORIGIN_DESTINATION"):
        validate_route_request(_req(destination={"text": " ambala "}), NOW)
    with raises(ApiError, "SAME_ORIGIN_DESTINATION"):
        validate_route_request(_req(origin={"latitude": 30.37, "longitude": 76.77},
                                    destination={"latitude": 30.37, "longitude": 76.77}), NOW)


def test_parse_and_validate_route_geometry():
    pts = parse_and_validate_route_geometry({"type": "LineString", "coordinates": [[76.0, 29.0], [76.1, 29.1]]})
    assert pts == [(76.0, 29.0), (76.1, 29.1)]
    for bad in ({"type": "Point", "coordinates": [1, 2]}, {"type": "LineString", "coordinates": [[76, 29]]},
                {"type": "LineString", "coordinates": [[76, 29], [999, 29]]},
                {"type": "LineString", "coordinates": [[76, 29], [76, 29]]},
                {"type": "LineString", "coordinates": [[76, 29], ["a", 29]]}, None):
        with raises(ValueError):
            parse_and_validate_route_geometry(bad)


# Synthetic straight line Delhi -> Ambala (TEST geometry; the district polygons are the real dataset).
LINE = [(77.2090, 28.6139), (77.0188, 28.9931), (76.9790, 29.3909), (76.9900, 29.6857), (76.8300, 30.1), (76.7794, 30.3752)]


def test_match_route_to_districts_in_order():
    m = match_route(DS.index, LINE)
    names = [DS.names[d] for d in m["district_ids"]]
    assert names == ["Sonipat", "Panipat", "Karnal", "Kurukshetra", "Ambala"], names
    matched = [s for s in m["sections"] if s["status"] == "matched"]
    assert abs(sum(s["length_m"] for s in m["sections"]) - m["total_length_m"]) < 1.0
    assert all(len(s["geometry"]["coordinates"]) >= 2 for s in m["sections"])
    assert matched[0]["start_m"] >= 0


def test_unmatched_route_section_is_unknown():
    m = match_route(DS.index, LINE)
    assert m["sections"][0]["status"] == "unknown" and m["sections"][0]["district_id"] is None
    assert 0 < m["coverage_ratio"] < 1
    outside = match_route(DS.index, [(77.17, 31.7), (76.9, 31.9)])      # Himachal Pradesh area
    assert outside["district_ids"] == [] and outside["coverage_ratio"] == 0.0


def _route(duration=3600):
    return {"route_id": "rt_a", "duration_s": duration}


def _full_match():
    # inside Karnal only
    return match_route(DS.index, [(76.95, 29.60), (76.99, 29.70)])


def test_weather_validity_overlaps_travel_period():
    karnal = ID["Karnal"]
    m = _full_match()
    cases = [(-5, -1, False), (-1, 0.5, True), (0.5, 3, True), (3, 5, False)]  # hours relative to departure; window = 1h
    for s, e, applies in cases:
        rec = make_record(district=karnal, start_h=1 + s, end_h=1 + e)        # departure is NOW+1h
        r = assess_route(_route(), m, snapshot([rec], (karnal,)), DEP, NOW)
        assert (r["risk_level"] == "orange") == applies, (s, e, r["risk_level"])


def test_expired_weather_record_is_not_current():
    karnal = ID["Karnal"]
    rec = make_record(district=karnal, start_h=-10, end_h=-5)
    assert display_state(rec, NOW) == "expired"
    r = assess_route(_route(), _full_match(), snapshot([rec], (karnal,)), DEP, NOW)
    assert r["risk_level"] == "green" and r["findings"] == []     # complete coverage + only an expired record
    uncertain = make_record(district=karnal, valid=False)
    r2 = assess_route(_route(), _full_match(), snapshot([uncertain], (karnal,)), DEP, NOW)
    assert r2["risk_level"] == "unknown"


def test_highest_applicable_warning_determines_risk():
    karnal = ID["Karnal"]
    recs = [make_record("T1", karnal, "yellow", start_h=1, end_h=4), make_record("T2", karnal, "red", start_h=1, end_h=4),
            make_record("T3", karnal, "orange", start_h=1, end_h=4)]
    r = assess_route(_route(), _full_match(), snapshot(recs, (karnal,)), DEP, NOW)
    assert r["risk_level"] == "red" and r["affected_districts"] == [karnal] and "wx:T2" in r["evidence_refs"]


def test_incomplete_coverage_never_returns_confirmed_green():
    karnal = ID["Karnal"]
    # (a) weather unavailable
    for status in ("not_configured", "unavailable"):
        r = assess_route(_route(), _full_match(), snapshot([], (), status=status), DEP, NOW)
        assert r["risk_level"] == "unknown" and r["coverage_status"] == "unavailable"
    # (b) route partly outside verified boundaries, no warnings
    r = assess_route(_route(), match_route(DS.index, LINE), snapshot([], tuple(DS.names)), DEP, NOW)
    assert r["risk_level"] == "unknown" and r["coverage_status"] == "partial"
    # (c) weather source did not cover the district
    r = assess_route(_route(), _full_match(), snapshot([], ()), DEP, NOW)
    assert r["risk_level"] == "unknown"
    # (d) boundary-uncertain district (Hisar)
    hisar = ID["Hisar"]
    m = match_route(DS.index, [(75.6, 29.1), (75.8, 29.2)])
    assert hisar in m["district_ids"], m["district_ids"]
    r = assess_route(_route(), m, snapshot([], (hisar,)), DEP, NOW, uncertain_district_ids=DS.uncertain_ids)
    assert r["risk_level"] != "green"
    # (e) control: complete coverage, covered, no warnings -> green is allowed
    r = assess_route(_route(), _full_match(), snapshot([], (karnal,)), DEP, NOW)
    assert r["risk_level"] == "green" and r["coverage_status"] == "complete"
    # (f) stale snapshot
    old = snapshot([], (karnal,), base=NOW - timedelta(hours=12))
    r = assess_route(_route(), _full_match(), old, DEP, NOW, max_weather_age_s=6 * 3600)
    assert r["risk_level"] == "unknown"


def test_duplicate_idempotent_request_does_not_duplicate_result():
    store, calls = IdempotencyStore(), []
    def fn():
        calls.append(1)
        return {"id": len(calls)}
    a, ra = store.execute("s", "key-12345678", "fp1", fn)
    b, rb = store.execute("s", "key-12345678", "fp1", fn)
    assert a == b and not ra and rb and len(calls) == 1
    with raises(ApiError, "IDEMPOTENCY_CONFLICT"):
        store.execute("s", "key-12345678", "fp2", fn)
    store.execute("s", "other-key-123", "fp1", fn)
    assert len(calls) == 2


# ---- extra tests ----
def test_risk_engine_is_deterministic():
    karnal = ID["Karnal"]
    snap = snapshot([make_record(district=karnal, start_h=1, end_h=3)], (karnal,))
    a = assess_route(_route(), _full_match(), snap, DEP, NOW)
    b = assess_route(_route(), _full_match(), snap, DEP, NOW)
    assert a == b


def test_comparison_never_claims_weather_basis_without_documented_levels():
    def summ(i, dur): return {"route_id": i, "label": i, "distance_m": 1000, "duration_s": dur}
    def res(i, risk, cov="complete"):
        return {"route_id": i, "risk_level": risk, "coverage_status": cov, "geographic_coverage_ratio": 1.0,
                "districts_traversed": ["HR-1"], "affected_districts": []}
    c = compare_routes([summ("a", 10), summ("b", 20)], [res("a", "unknown", "unavailable"), res("b", "unknown", "unavailable")])
    assert c["basis"] == "geographic_only" and c["lowest_documented_exposure_route_ids"] == [] and c["fastest_route_id"] == "a"
    c = compare_routes([summ("a", 10), summ("b", 20)], [res("a", "red"), res("b", "unknown", "partial")])
    assert c["basis"] == "geographic_only"          # an unknown route could be better or worse
    c = compare_routes([summ("a", 10), summ("b", 20)], [res("a", "red"), res("b", "yellow")])
    assert c["basis"] == "weather" and c["lowest_documented_exposure_route_ids"] == ["b"]
    c = compare_routes([summ("a", 10)], [res("a", "green")])
    assert c["basis"] == "none"


def test_dataset_loaded_from_real_geojson():
    assert len(DS.by_id) == 22 and DS.metadata["coverage_status"] == "partial"
    assert DS.metadata["missing_districts"] == ["Hansi"] and DS.metadata["boundary_vintage"] is None
    assert not DS.rejected and all(i.startswith("HR-") for i in DS.by_id)
