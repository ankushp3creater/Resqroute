"""HTTP contract tests (need: pip install -r backend/requirements-dev.txt). Fake transports = TEST ONLY."""
import json
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.tests.test_workflow import SCRIPT, dep, make_state  # noqa: E402


def client(script=SCRIPT):
    return TestClient(create_app(make_state(script)))


def test_health_has_integrations_and_no_secrets():
    st = make_state()
    st.settings.gemini_api_key = "SECRET-TEST-KEY"
    st.router.probe = lambda: (True, "ok")
    st.geocoder.probe = lambda: (True, "ok")
    st.gemini.probe = lambda: ("connected", "ok")
    r = TestClient(create_app(st)).get("/api/health")
    assert r.status_code == 200 and "SECRET-TEST-KEY" not in r.text
    body = r.json()
    assert set(body["integrations"]) >= {"routing", "imd", "gemini", "geography"} and body["checked_at"]
    assert body["integrations"]["imd"]["status"] == "not_configured"


def test_geography_endpoint():
    r = client().get("/api/geography/haryana/districts")
    j = r.json()
    assert r.status_code == 200 and len(j["features"]) == 22 and j["metadata"]["coverage_status"] == "partial"
    assert j["metadata"]["missing_districts"] == ["Hansi"]


def test_validation_and_error_contract():
    c = client()
    r = c.post("/api/routes", json={"origin": {"text": ""}, "destination": {"text": "B"}, "departure_time": dep()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ROUTE_INPUT"
    assert c.post("/api/routes", content="not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert c.get("/api/route-assessments/as_missing").json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert c.get("/api/nope").json()["error"]["code"] == "NOT_FOUND"


def test_body_limit():
    r = client().post("/api/routes", content=b"x" * 70000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_routes_assessment_and_idempotency():
    c = client()
    body = {"origin": {"text": "A"}, "destination": {"text": "B"}, "departure_time": dep()}
    h = {"Idempotency-Key": "key-aaaaaaaa"}
    r1 = c.post("/api/routes", json=body, headers=h)
    r2 = c.post("/api/routes", json=body, headers=h)       # replay: no second geocode/route call (script would be exhausted)
    assert r1.status_code == 200 and r2.json() == r1.json() and r2.headers.get("Idempotent-Replay") == "true"
    assert c.post("/api/routes", json=dict(body, destination={"text": "C"}), headers=h).status_code == 409
    ids = [x["route_id"] for x in r1.json()["routes"]]
    a = c.post("/api/route-assessments", json={"route_ids": ids, "departure_time": dep()})
    assert a.status_code == 200 and a.json()["results"][0]["risk_level"] == "unknown"
    assert c.get(f"/api/route-assessments/{a.json()['assessment_id']}").status_code == 200
    ai = c.post(f"/api/route-assessments/{a.json()['assessment_id']}/ai-analysis")
    assert ai.status_code == 503 and ai.json()["error"]["code"] == "AI_NOT_CONFIGURED"
    w = c.get("/api/weather/districts/HR-058")
    assert w.status_code == 200 and w.json()["summary"] == "unavailable" and w.json()["records"] == []
    assert c.post("/api/data/refresh").json()["sources"][0]["status"] == "not_configured"
