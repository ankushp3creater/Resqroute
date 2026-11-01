"""Input validation (spec I1-I4, I9, E1-E4). Raises ApiError(422, ...) with field-specific details."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.errors import ApiError
from backend.models.records import clean_text

MAX_TEXT = 200
MAX_ROUTE_IDS = 10
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:]{8,128}$")
MAX_FUTURE_DAYS = 365


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _validate_location(value: Any, field: str, errors: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        errors[field] = f"Please enter a {field}."
        return None
    text = value.get("text")
    lat, lon = value.get("latitude"), value.get("longitude")
    clean = None
    if text is not None:
        if not isinstance(text, str):
            errors[field] = f"{field.capitalize()} text must be a string."
            return None
        clean = clean_text(text, MAX_TEXT + 1)
        if len(clean) > MAX_TEXT:
            errors[field] = f"{field.capitalize()} must be at most {MAX_TEXT} characters."
            return None
        clean = clean or None
    has_lat, has_lon = lat is not None, lon is not None
    if has_lat != has_lon:
        errors[field] = "Both latitude and longitude are required when coordinates are supplied."
        return None
    if has_lat:
        if not (_is_number(lat) and _is_number(lon)):
            errors[field] = "Latitude and longitude must be numbers."
            return None
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            errors[field] = "Coordinates are out of range (latitude -90..90, longitude -180..180)."
            return None
        return {"text": clean, "latitude": float(lat), "longitude": float(lon)}
    if not clean:
        errors[field] = f"Please enter a {field}."
        return None
    return {"text": clean, "latitude": None, "longitude": None}


def parse_departure(value: Any, now: datetime, grace_seconds: int) -> datetime:
    msg = "Please enter a valid departure date and time."
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "INVALID_DEPARTURE_TIME", msg, {"fields": {"departure_time": msg}})
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ApiError(422, "INVALID_DEPARTURE_TIME", msg, {"fields": {"departure_time": msg}})
    if dt.tzinfo is None or dt.utcoffset() is None:
        m = "Departure time must include a timezone offset, e.g. 2026-11-02T08:00:00+05:30."
        raise ApiError(422, "INVALID_DEPARTURE_TIME", m, {"fields": {"departure_time": m}})
    if dt < now - timedelta(seconds=grace_seconds):
        m = "Departure time is in the past. Please choose a current or future time."
        raise ApiError(422, "INVALID_DEPARTURE_TIME", m, {"fields": {"departure_time": m}})
    if dt > now + timedelta(days=MAX_FUTURE_DAYS):
        m = f"Departure time must be within {MAX_FUTURE_DAYS} days."
        raise ApiError(422, "INVALID_DEPARTURE_TIME", m, {"fields": {"departure_time": m}})
    return dt


def validate_route_request(payload: Any, now: datetime, grace_seconds: int = 300) -> Dict[str, Any]:
    """L-B1: validate origin, destination, departure time before any processing."""
    if not isinstance(payload, dict):
        raise ApiError(422, "INVALID_ROUTE_INPUT", "Request body must be a JSON object.", {"fields": {}})
    errors: Dict[str, str] = {}
    origin = _validate_location(payload.get("origin"), "origin", errors)
    destination = _validate_location(payload.get("destination"), "destination", errors)
    if errors:
        both_missing = all("Please enter" in m for m in errors.values()) and len(errors) >= 1
        message = "Please enter both origin and destination." if both_missing else "; ".join(errors.values())
        raise ApiError(422, "INVALID_ROUTE_INPUT", message, {"fields": errors})
    # Identical locations (E3) - exact comparison of what the user supplied
    same = False
    if origin["latitude"] is not None and destination["latitude"] is not None:
        same = (round(origin["latitude"], 5), round(origin["longitude"], 5)) == (
            round(destination["latitude"], 5), round(destination["longitude"], 5))
    elif origin["latitude"] is None and destination["latitude"] is None:
        same = origin["text"].casefold() == destination["text"].casefold()
    if same:
        m = "Origin and destination must be different."
        raise ApiError(422, "SAME_ORIGIN_DESTINATION", m, {"fields": {"destination": m}})
    departure = parse_departure(payload.get("departure_time"), now, grace_seconds)
    return {"origin": origin, "destination": destination, "departure_time": departure}


def validate_assessment_request(payload: Any, now: datetime, grace_seconds: int = 300) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(422, "INVALID_ROUTE_IDS", "Request body must be a JSON object.", {"fields": {}})
    ids = payload.get("route_ids")
    if not isinstance(ids, list) or not (1 <= len(ids) <= MAX_ROUTE_IDS):
        m = f"route_ids must be a list of 1 to {MAX_ROUTE_IDS} route IDs."
        raise ApiError(422, "INVALID_ROUTE_IDS", m, {"fields": {"route_ids": m}})
    for rid in ids:
        if not isinstance(rid, str) or not _ID_RE.match(rid):
            m = "Every route ID must be a string of letters, digits, '_' or '-'."
            raise ApiError(422, "INVALID_ROUTE_IDS", m, {"fields": {"route_ids": m}})
    if len(set(ids)) != len(ids):
        m = "route_ids must not contain duplicates."
        raise ApiError(422, "INVALID_ROUTE_IDS", m, {"fields": {"route_ids": m}})
    departure = parse_departure(payload.get("departure_time"), now, grace_seconds)
    return {"route_ids": list(ids), "departure_time": departure}


def validate_id(value: Any, what: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"This {what} could not be found.", {})
    return value


def validate_idempotency_key(key: Optional[str]) -> Optional[str]:
    if key is None:
        return None
    if not IDEMPOTENCY_KEY_RE.match(key):
        raise ApiError(422, "INVALID_IDEMPOTENCY_KEY",
                       "Idempotency-Key must be 8-128 characters (letters, digits, _ - . :).", {})
    return key
