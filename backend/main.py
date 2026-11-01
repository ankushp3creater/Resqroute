"""FastAPI app factory. Run: uvicorn backend.main:app --reload --port 8000"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from backend.api.routes_api import router
from backend.config import ROOT_DIR
from backend.errors import ApiError, error_payload
from backend.state import AppState, build_state

log = logging.getLogger("resqroute")


class BodyLimitMiddleware:
    """Reject request bodies over the limit (spec: 64 KB)."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cl = dict(scope["headers"]).get(b"content-length")
        too_big = False
        try:
            too_big = cl is not None and int(cl) > self.max
        except ValueError:
            too_big = True
        if too_big:
            return await self._reject(send)
        received = 0

        async def limited():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.max:
                    raise _TooLarge()
            return msg

        started = False

        async def tracking_send(m):
            nonlocal started
            started = started or m["type"] == "http.response.start"
            await send(m)

        try:
            await self.app(scope, limited, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send):
        resp = JSONResponse(error_payload("REQUEST_TOO_LARGE", "Request body is too large.", {}), status_code=413)
        await resp({"type": "http", "method": "POST", "headers": []}, None, send)


class _TooLarge(Exception):
    pass


def create_app(state: Optional[AppState] = None) -> FastAPI:
    state = state or build_state()
    app = FastAPI(title="ResQRoute API", version="0.1.0")
    app.state.rq = state
    app.add_middleware(BodyLimitMiddleware, max_bytes=state.settings.max_body_bytes)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(CORSMiddleware, allow_origins=state.settings.cors_allowed_origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Idempotency-Key"],
                       expose_headers=["Idempotent-Replay"])

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        log.warning("api_error code=%s status=%s", exc.code, exc.status)
        return JSONResponse(error_payload(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = [{"loc": [str(x) for x in e.get("loc", [])], "msg": str(e.get("msg", ""))[:200]} for e in exc.errors()[:10]]
        return JSONResponse(error_payload("INVALID_REQUEST", "The request body is missing or malformed.", {"errors": fields}),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(error_payload(code, "Resource or method not available.", {}), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.error("unhandled %s", type(exc).__name__)   # never log payloads or coordinates
        return JSONResponse(error_payload("INTERNAL_ERROR", "An unexpected error occurred.", {}), status_code=500)

    app.include_router(router)
    dist = ROOT_DIR / "frontend" / "dist"
    if dist.is_dir():   # serve the production build from one process
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    return app


def __getattr__(name):   # lazy so importing backend.main in tests doesn't build state
    if name == "app":
        return create_app()
    raise AttributeError(name)
