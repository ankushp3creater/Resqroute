"""End-to-end workflow with FAKE external transports (TEST ONLY) and the REAL boundary dataset."""
from datetime import timedelta

from backend.config import Settings
from backend.errors import ApiError
from backend.models.records import utcnow
from backend.services import workflow
from backend.services.ai.gemini_client import GeminiClient
from backend.services.geography.loader import load_dataset
from backend.services.routing.geocoder import Geocoder
from backend.services.routing.osrm import OsrmProvider
from backend.services.weather.base import WeatherService
from backend.services.weather.imd.client import ImdProvider
from backend.state import AppState
from backend.tests.helpers import FakeHttp, FixedWeatherProvider, gemini_call, gemini_text, raises, snapshot, make_record
from backend.tests.test_core import DATA, LINE
import json

def osrm_ok():
    return (200, {"code": "Ok", "routes": [
        {"distance": 210000, "duration": 9000, "geometry": {"type": "LineString", "coordinates": [list(p) for p in LINE]}},
        {"distance": 230000, "duration": 8700, "geometry": {"type": "LineString", "coordinates": [list(p) for p in LINE[::-1]]}}]})

def nominatim(lat, lon, name):
    return (200, [{"lat": str(lat), "lon": str(lon), "display_name": name}])

def make_state(http_script=(), weather=None, gemini_script=None, key="TEST"):
    s = Settings(gemini_api_key=key if gemini_script is not None else "")
    st = AppState(settings=s, geocoder=Geocoder(s, FakeHttp(*http_script[:2]), sleep=lambda _: None),
                  router=OsrmProvider(s, FakeHttp(*http_script[2:])),
                  weather=WeatherService(weather or ImdProvider(s, FakeHttp()), 600),
                  gemini=GeminiClient(s, FakeHttp(*(gemini_script or []))))
    st.dataset = load_dataset(DATA)
    return st

def dep():
    return (utcnow() + timedelta(hours=1)).isoformat()

def search(st):
    return workflow.create_route_request(st, {"origin": {"text": "A"}, "destination": {"text": "B"}, "departure_time": dep()})

SCRIPT = (nominatim(28.61, 77.20, "Delhi"), nominatim(30.37, 76.77, "Ambala"), osrm_ok())


def test_full_journey_without_imd_is_honest_unknown():
    st = make_state(SCRIPT)
    r = search(st)
    assert len(r["routes"]) == 2 and r["routes"][0]["district_ids"] and r["routes"][0]["sections"]
    a = workflow.create_assessment(st, {"route_ids": [x["route_id"] for x in r["routes"]], "departure_time": dep()})
    assert a["weather"]["status"] == "not_configured"
    assert all(x["risk_level"] == "unknown" and x["coverage_status"] == "unavailable" for x in a["results"])
    assert a["comparison"]["basis"] == "geographic_only" and a["status"] == "partial"
    assert {e["type"] for e in a["trace"]} >= {"state", "source", "processing"}
    with raises(ApiError, "AI_NOT_CONFIGURED"):
        workflow.run_ai_analysis(st, a["assessment_id"])
    assert workflow.get_assessment(st, a["assessment_id"])["results"][0]["risk_level"] == "unknown"   # preserved


def test_full_journey_with_test_weather_and_agent():
    st0 = make_state(SCRIPT)
    r = search(st0)
    # TEST-ONLY synthetic warning on Karnal for route 1
    karnal = next(i for i, n in st0.dataset.names.items() if n == "Karnal")
    snap = snapshot([make_record("T-K", karnal, "red", start_h=0, end_h=48, base=utcnow())], tuple(st0.dataset.names), base=utcnow())
    final = {"summary": "Both routes cross Karnal where a red warning applies (wx:T-K).", "analysis_basis": "geographic_only",
             "recommended_route_id": None, "recommendation_reason": "Both routes share the same documented exposure.",
             "geographic_comparison": "Route 2 is faster; not a safety judgment.", "geographic_preference_route_id": None,
             "evidence_refs": ["wx:T-K"], "limitations": ["Coverage incomplete outside Haryana."]}
    st = make_state(SCRIPT, FixedWeatherProvider(snap),
                    gemini_script=[gemini_call("get_weather_evidence", {}), gemini_text(json.dumps(final))])
    r = search(st)
    a = workflow.create_assessment(st, {"route_ids": [x["route_id"] for x in r["routes"]], "departure_time": dep()})
    assert all(x["risk_level"] == "red" for x in a["results"]) and a["comparison"]["basis"] == "geographic_only"
    out = workflow.run_ai_analysis(st, a["assessment_id"])
    assert out["ai_generated"] and out["tool_trace"][0]["tool"] == "get_weather_evidence"
    assert a["status"] == a["deterministic_status"] and a["ai"]["status"] == "completed"


def test_no_route_and_validation_errors_and_unknown_ids():
    st = make_state((nominatim(1, 2, "x"), nominatim(3, 4, "y"), (200, {"code": "NoRoute"})))
    assert search(st)["routes"] == []
    st = make_state(((200, []),))
    with raises(ApiError, "LOCATION_NOT_FOUND"):
        search(st)
    with raises(ApiError, "RESOURCE_NOT_FOUND"):
        workflow.create_assessment(make_state(), {"route_ids": ["rt_nope"], "departure_time": dep()})
    with raises(ApiError, "RESOURCE_NOT_FOUND"):
        workflow.get_assessment(make_state(), "as_nope")


def test_provider_failures_are_structured():
    from backend.http import UpstreamTimeout, UpstreamError
    st = make_state((nominatim(1, 2, "x"), nominatim(3, 4, "y"), UpstreamTimeout()))
    with raises(ApiError, "ROUTING_TIMEOUT"):
        search(st)
    st = make_state((nominatim(1, 2, "x"), nominatim(3, 4, "y"), (200, {"code": "Ok", "routes": [{"distance": 5, "duration": 5, "geometry": None}]})))
    with raises(ApiError, "ROUTING_PROVIDER_ERROR"):
        search(st)
    st = make_state((UpstreamError(None, "x"),))
    with raises(ApiError, "GEOCODER_ERROR"):
        search(st)


def test_imd_extension_point_reports_status_honestly():
    s = Settings()
    assert ImdProvider(s, FakeHttp()).fetch().status == "not_configured"
    s2 = Settings(imd_api_base_url="https://example.invalid")
    snap = ImdProvider(s2, FakeHttp()).fetch()
    assert snap.status == "unavailable" and snap.records == [] and not snap.usable
    d = workflow.district_weather(make_state(), next(iter(make_state().dataset.by_id)), None)
    assert d["summary"] == "unavailable" and d["records"] == []
