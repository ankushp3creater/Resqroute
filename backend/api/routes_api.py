"""HTTP layer: thin routes only. All logic lives in services/ (spec 6.2)."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, Request, Response

from backend.errors import ApiError
from backend.models.validation import validate_id, validate_idempotency_key
from backend.services import workflow

router = APIRouter(prefix="/api")


def _st(request: Request):
    return request.app.state.rq


def _idem(request: Request, response: Response, scope: str, key: Optional[str], fingerprint: str, fn):
    key = validate_idempotency_key(key)
    result, replay = _st(request).idempotency.execute(scope, key, fingerprint, fn)
    if replay:
        response.headers["Idempotent-Replay"] = "true"
    return result


@router.get("/health")
def health(request: Request):
    return _st(request).health()


@router.get("/geography/haryana/districts")
def districts(request: Request):
    st = _st(request)
    if st.dataset is None:
        raise ApiError(503, "GEOGRAPHY_UNAVAILABLE", "District boundary data is currently unavailable.",
                       {"detail": st.dataset_error})
    return Response(content=st.dataset.api_bytes, media_type="application/json")


@router.post("/routes")
def post_routes(request: Request, response: Response, payload: Dict[str, Any] = Body(...),
                idempotency_key: Optional[str] = Header(default=None)):
    st = _st(request)
    fp = json.dumps(payload, sort_keys=True, default=str)
    return _idem(request, response, "POST /routes", idempotency_key, fp, lambda: workflow.create_route_request(st, payload))


@router.get("/routes/{route_id}")
def get_route(request: Request, route_id: str):
    return workflow.get_route(_st(request), validate_id(route_id, "route"))


@router.get("/weather/districts/{district_id}")
def weather_district(request: Request, district_id: str, date: Optional[str] = None):
    return workflow.district_weather(_st(request), validate_id(district_id, "district"), date)


@router.post("/route-assessments")
def post_assessment(request: Request, response: Response, payload: Dict[str, Any] = Body(...),
                    idempotency_key: Optional[str] = Header(default=None)):
    st = _st(request)
    fp = json.dumps(payload, sort_keys=True, default=str)
    return _idem(request, response, "POST /route-assessments", idempotency_key, fp,
                 lambda: workflow.create_assessment(st, payload))


@router.get("/route-assessments/{assessment_id}")
def get_assessment(request: Request, assessment_id: str):
    return workflow.get_assessment(_st(request), validate_id(assessment_id, "assessment"))


@router.post("/route-assessments/{assessment_id}/ai-analysis")
def post_ai(request: Request, response: Response, assessment_id: str,
            idempotency_key: Optional[str] = Header(default=None)):
    st = _st(request)
    aid = validate_id(assessment_id, "assessment")
    return _idem(request, response, f"POST /ai-analysis/{aid}", idempotency_key, aid,
                 lambda: workflow.run_ai_analysis(st, aid))


@router.post("/data/refresh")
def refresh(request: Request):
    return workflow.refresh_data(_st(request))
