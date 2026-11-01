"""API error contract (spec Q8): {error: {code, message, details}}."""
from __future__ import annotations

from typing import Any, Dict, Optional


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_payload(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}
