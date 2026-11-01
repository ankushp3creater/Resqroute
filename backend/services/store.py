"""In-memory store (spec: no permanent user-data DB; cleared on restart) + idempotency (E17, AC-B14)."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from backend.errors import ApiError

MAX_ITEMS = 200


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.route_requests: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.routes: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.assessments: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.ai_locks: Dict[str, threading.Lock] = {}

    def _trim(self, od: "OrderedDict"):
        while len(od) > MAX_ITEMS:
            od.popitem(last=False)

    def put_route_request(self, req: Dict[str, Any], routes: list):
        with self.lock:
            self.route_requests[req["route_request_id"]] = req
            for r in routes:
                self.routes[r["route_id"]] = r
            self._trim(self.route_requests)
            self._trim(self.routes)

    def get_route(self, route_id: str) -> Dict[str, Any]:
        with self.lock:
            r = self.routes.get(route_id)
        if not r:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "This route could not be found. Please search again.", {})
        return r

    def get_route_request(self, rid: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.route_requests.get(rid)

    def put_assessment(self, a: Dict[str, Any]):
        with self.lock:
            self.assessments[a["assessment_id"]] = a
            self._trim(self.assessments)

    def get_assessment(self, aid: str) -> Dict[str, Any]:
        with self.lock:
            a = self.assessments.get(aid)
        if not a:
            raise ApiError(404, "RESOURCE_NOT_FOUND",
                           "This route assessment could not be found. Please generate a new assessment.", {})
        return a

    def ai_lock(self, aid: str) -> threading.Lock:
        with self.lock:
            return self.ai_locks.setdefault(aid, threading.Lock())


class IdempotencyStore:
    """Same key + same input -> stored result (no duplicate work). Same key + different input -> 409."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def execute(self, scope: str, key: Optional[str], fingerprint: str,
                fn: Callable[[], Any]) -> Tuple[Any, bool]:
        if not key:
            return fn(), False
        with self._lock:
            ent = self._entries.setdefault((scope, key), {"fp": None, "result": None, "done": False,
                                                           "lock": threading.Lock()})
            if len(self._entries) > 1000:
                for k in list(self._entries)[:200]:
                    if self._entries[k]["done"] and k != (scope, key):
                        del self._entries[k]
        with ent["lock"]:   # serialises concurrent duplicates: the second waits, then replays
            if ent["done"]:
                if ent["fp"] != fingerprint:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT",
                                   "This Idempotency-Key was already used with different input.", {})
                return ent["result"], True
            result = fn()
            ent.update(fp=fingerprint, result=result, done=True)
            return result, False
