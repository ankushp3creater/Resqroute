"""Read-only agent tools (L-B10). Every tool name, argument and ID is validated before execution.

Tools only read the stored, already-validated assessment. They cannot modify data, call external
services or take actions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

ALLOWED_TOOLS = ("get_route_details", "get_weather_evidence", "compare_route_assessments", "get_data_limitations")


class ToolError(Exception):
    pass


def declarations() -> List[Dict[str, Any]]:
    route_id = {"type": "STRING", "description": "A route_id from this assessment. Omit to cover all routes."}
    return [
        {"name": "get_route_details",
         "description": "Geographic facts for a route: distance, duration, ordered districts crossed with lengths, "
                        "boundary coverage. Returns evidence_ref values you may cite.",
         "parameters": {"type": "OBJECT", "properties": {"route_id": route_id}}},
        {"name": "get_weather_evidence",
         "description": "Official weather evidence for a route: source status, applicable warnings, districts with "
                        "and without weather data, and the deterministic risk level. If weather is unavailable, "
                        "the result says so; that is NOT evidence of safety.",
         "parameters": {"type": "OBJECT", "properties": {
             "route_id": route_id,
             "district_id": {"type": "STRING", "description": "Optional district_id to narrow the evidence."}}}},
        {"name": "compare_route_assessments",
         "description": "The backend's deterministic comparison of routes (risk, coverage, distance, duration) and "
                        "whether a weather-based preference is supportable (basis).",
         "parameters": {"type": "OBJECT", "properties": {
             "route_ids": {"type": "ARRAY", "items": {"type": "STRING"},
                           "description": "Optional subset of route_ids."}}}},
        {"name": "get_data_limitations",
         "description": "Known data gaps and uncertainties (assessment-wide or for one route), each with an "
                        "evidence_ref (lim:N).",
         "parameters": {"type": "OBJECT", "properties": {"route_id": route_id}}},
    ]


class ToolBox:
    def __init__(self, assessment: Dict[str, Any]):
        self.a = assessment
        self.summaries = {s["route_id"]: s for s in assessment["route_summaries"]}
        self.results = {r["route_id"]: r for r in assessment["results"]}
        self.district_ids = {d for r in assessment["results"] for d in r["districts_traversed"]}

    def _route_id(self, value: Any, required: bool = False) -> Optional[str]:
        if value is None or value == "":
            if required:
                raise ToolError("route_id is required")
            return None
        if not isinstance(value, str) or value not in self.summaries:
            raise ToolError(f"unknown route_id {str(value)[:40]!r}; valid: {list(self.summaries)}")
        return value

    def execute(self, name: Any, args: Any) -> Dict[str, Any]:
        if name not in ALLOWED_TOOLS:
            raise ToolError(f"tool {str(name)[:40]!r} is not permitted")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        allowed_keys = {"get_route_details": {"route_id"}, "get_weather_evidence": {"route_id", "district_id"},
                        "compare_route_assessments": {"route_ids"}, "get_data_limitations": {"route_id"}}[name]
        extra = set(args) - allowed_keys
        if extra:
            raise ToolError(f"unexpected argument(s): {sorted(extra)}")
        return getattr(self, "_" + name)(args)

    # ---- tools ----
    def _get_route_details(self, args):
        rid = self._route_id(args.get("route_id"))
        ids = [rid] if rid else list(self.summaries)
        return {"routes": [self.summaries[i] for i in ids]}

    def _get_weather_evidence(self, args):
        rid = self._route_id(args.get("route_id"))
        did = args.get("district_id")
        if did is not None and did not in self.district_ids:
            raise ToolError(f"unknown district_id {str(did)[:40]!r}")
        ids = [rid] if rid else list(self.results)
        weather = self.a["weather"]
        out = []
        for i in ids:
            res = self.results[i]
            findings = [f for f in res["findings"] if did is None or f["district_id"] == did]
            if weather["status"] in ("not_configured", "unavailable"):
                interp = ("Official weather data is UNAVAILABLE. No warnings could be retrieved. This does NOT mean "
                          "there is no weather risk; the risk level is 'unknown'.")
            elif findings:
                interp = "Applicable official warnings were found for the travel window (see applicable_warnings)."
            else:
                interp = ("No applicable official warning was found for the checked districts and travel window. "
                          "This is not a guarantee of safety.")
            out.append({
                "route_id": i, "risk_level": res["risk_level"], "coverage_status": res["coverage_status"],
                "weather_source": weather["source_name"], "weather_source_status": weather["status"],
                "weather_retrieved_at": weather["retrieved_at"],
                "districts_with_weather_data": res["weather_coverage"]["districts_with_data"],
                "districts_without_weather_data": res["weather_coverage"]["districts_without_data"],
                "applicable_warnings": findings, "interpretation": interp,
            })
        return {"evidence": out}

    def _compare_route_assessments(self, args):
        ids = args.get("route_ids")
        comp = self.a["comparison"]
        if ids is None:
            return {"comparison": comp}
        if not isinstance(ids, list) or not ids or len(ids) > 10:
            raise ToolError("route_ids must be a non-empty list")
        for i in ids:
            self._route_id(i, required=True)
        sub = dict(comp)
        sub["routes"] = [r for r in comp["routes"] if r["route_id"] in ids]
        return {"comparison": sub}

    def _get_data_limitations(self, args):
        rid = self._route_id(args.get("route_id"))
        items = [x for x in self.a["limitation_index"] if rid is None or x["scope"] in ("assessment", rid)]
        return {"limitations": items}
