"""Agent tests use a SCRIPTED fake Gemini transport (TEST ONLY). They prove loop/validation logic,
NOT that the live Gemini API works."""
import json
from datetime import timedelta

from backend.config import Settings
from backend.services.ai.agent import run_agent, validate_analysis
from backend.services.ai.gemini_client import AiNotConfigured, GeminiClient, InvalidAiResponse
from backend.tests.helpers import FakeHttp, gemini_call, gemini_text, raises


def fake_assessment(basis="geographic_only", lowest=()):
    return {
        "assessment_id": "as_test", "departure_time": "2026-11-02T08:00:00+00:00",
        "weather": {"status": "not_configured", "source_name": "IMD"},
        "comparison": {"basis": basis, "lowest_documented_exposure_route_ids": list(lowest), "routes": [
            {"route_id": "rt_a"}, {"route_id": "rt_b"}], "notes": []},
        "route_summaries": [{"route_id": "rt_a", "label": "Route 1", "distance_m": 1000, "duration_s": 100, "evidence_ref": "route:rt_a", "districts": []},
                            {"route_id": "rt_b", "label": "Route 2", "distance_m": 1200, "duration_s": 90, "evidence_ref": "route:rt_b", "districts": []}],
        "results": [{"route_id": r, "risk_level": "unknown", "coverage_status": "unavailable", "findings": [],
                     "districts_traversed": [], "weather_coverage": {"districts_with_data": [], "districts_without_data": []}}
                    for r in ("rt_a", "rt_b")],
        "limitation_index": [{"evidence_ref": "lim:0", "scope": "assessment", "text": "IMD not configured"}],
        "evidence_index": {"route:rt_a": {}, "route:rt_b": {}, "lim:0": {}},
    }


GOOD = {"summary": "Weather data is unavailable, so only geographic comparison is possible.", "analysis_basis": "geographic_only",
        "recommended_route_id": None, "recommendation_reason": "No weather-based preference can be made.",
        "geographic_comparison": "Route 2 is faster (90 s vs 100 s) but longer; this is not a safety judgment.",
        "geographic_preference_route_id": "rt_b", "evidence_refs": ["route:rt_a", "route:rt_b", "lim:0"],
        "limitations": ["IMD not configured"]}


def client(*script):
    s = Settings(gemini_api_key="TEST-KEY")
    return GeminiClient(s, FakeHttp(*script)), s


def test_agent_runs_multi_step_tool_loop_and_validates_output():
    c, _ = client(gemini_call("get_data_limitations", {}), gemini_call("get_route_details", {}),
                  gemini_call("compare_route_assessments", {}), gemini_text(json.dumps(GOOD)))
    out = run_agent(fake_assessment(), c)
    tools = [t["tool"] for t in out["tool_trace"] if t["type"] == "tool_call"]
    assert tools == ["get_data_limitations", "get_route_details", "compare_route_assessments"]
    assert out["steps_used"] == 3 and out["recommended_route_id"] is None
    assert all(t["status"] == "ok" for t in out["tool_trace"] if t["type"] == "tool_call")
    assert "lim:0" in json.dumps(out["tool_trace"])          # real tool output is in the trace
    assert out["validation"]["refs_not_retrieved_by_tools"] == []


def test_invalid_ai_evidence_reference_is_rejected():
    bad = dict(GOOD, evidence_refs=["wx:DOES-NOT-EXIST"])
    with raises(ValueError):
        validate_analysis(bad, fake_assessment(), set())
    with raises(ValueError):
        validate_analysis(dict(GOOD, recommended_route_id="rt_zzz"), fake_assessment(), set())
    # recommendation without a weather basis is rejected
    with raises(ValueError):
        validate_analysis(dict(GOOD, recommended_route_id="rt_a"), fake_assessment(), set())
    # unsupported safety claim is rejected
    with raises(ValueError):
        validate_analysis(dict(GOOD, summary="Route 2 is completely safe."), fake_assessment(), set())
    ok = validate_analysis(GOOD, fake_assessment(), {"route:rt_a"})
    assert ok["validation"]["refs_not_retrieved_by_tools"] == ["lim:0", "route:rt_b"]


def test_agent_self_corrects_once_then_fails_closed():
    bad = dict(GOOD, evidence_refs=["wx:FAKE"])
    c, _ = client(gemini_text(json.dumps(bad)), gemini_text(json.dumps(GOOD)))
    out = run_agent(fake_assessment(), c)
    assert out["retries_used"] == 1 and any(t["type"] == "validation_rejected" for t in out["tool_trace"])
    c2, _ = client(gemini_text(json.dumps(bad)), gemini_text(json.dumps(bad)))
    with raises(InvalidAiResponse, "INVALID_AI_RESPONSE"):
        run_agent(fake_assessment(), c2)


def test_agent_rejects_forbidden_tools_and_bad_ids_and_caps_steps():
    script = [gemini_call("delete_everything", {}), gemini_call("get_route_details", {"route_id": "rt_hacked"})]
    script += [gemini_call("get_route_details", {}) for _ in range(5)] + [gemini_text(json.dumps(GOOD))]
    c, _ = client(*script)
    out = run_agent(fake_assessment(), c, max_steps=6)
    calls = [t for t in out["tool_trace"] if t["type"] == "tool_call"]
    assert calls[0]["status"] == "error" and calls[1]["status"] == "error"
    assert out["steps_used"] <= 6
    # model keeps asking for tools beyond the cap -> rejected, then forced to answer
    c2, _ = client(*([gemini_call("get_route_details", {})] * 3 + [gemini_text(json.dumps(GOOD))]))
    out2 = run_agent(fake_assessment(), c2, max_steps=2)
    statuses = [t["status"] for t in out2["tool_trace"] if t["type"] == "tool_call"]
    assert statuses == ["ok", "ok", "rejected"]


def test_missing_gemini_preserves_deterministic_assessment():
    gc = GeminiClient(Settings(gemini_api_key=""), FakeHttp())
    assert not gc.configured and gc.probe()[0] == "not_configured"
    with raises(AiNotConfigured, "AI_NOT_CONFIGURED"):
        run_agent(fake_assessment(), gc)
