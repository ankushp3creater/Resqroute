"""Routing provider: OSRM (swappable). Real road routes only - never fabricated (F4, L-B2)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from backend.config import Settings
from backend.errors import ApiError
from backend.http import HttpClient, UpstreamError, UpstreamTimeout
from backend.models.records import utcnow
from backend.services.routing.base import build_route_record


class OsrmProvider:
    def __init__(self, settings: Settings, http: Any = None):
        self.settings = settings
        self.http = http or HttpClient()
        host = urlparse(settings.routing_base_url).netloc or settings.routing_base_url
        self.name = f"OSRM ({host})"

    def _call(self, url: str, params: Dict[str, Any]) -> Tuple[int, Any]:
        try:
            return self.http.request_json("GET", url, params=params, timeout=self.settings.request_timeout_seconds)
        except UpstreamTimeout:
            raise ApiError(504, "ROUTING_TIMEOUT", "The routing service did not respond. Please try again.", {})
        except UpstreamError:
            raise ApiError(502, "ROUTING_PROVIDER_ERROR",
                           "A valid route could not be retrieved. No route assessment was created.", {})

    def get_routes(self, origin: Dict[str, Any], destination: Dict[str, Any], departure_time=None) -> List[Dict[str, Any]]:
        url = (f"{self.settings.routing_base_url}/route/v1/driving/"
               f"{origin['longitude']:.6f},{origin['latitude']:.6f};"
               f"{destination['longitude']:.6f},{destination['latitude']:.6f}")
        params = {"overview": "full", "geometries": "geojson", "steps": "false"}
        alt = self.settings.routing_alternatives
        params["alternatives"] = str(alt) if alt > 1 else ("true" if alt == 1 else "false")
        status, data = self._call(url, params)
        if isinstance(data, dict) and data.get("code") == "InvalidQuery" and alt > 1:
            params["alternatives"] = "true"  # older servers only accept true/false
            status, data = self._call(url, params)
        if not isinstance(data, dict):
            raise ApiError(502, "ROUTING_PROVIDER_ERROR", "A valid route could not be retrieved. No route assessment was created.", {})
        code = data.get("code")
        if code == "NoRoute":
            return []
        if code == "NoSegment":
            raise ApiError(422, "LOCATION_NOT_FOUND",
                           "We couldn't find a road near one of these locations. Try a more specific place name.", {})
        if status != 200 or code != "Ok" or not isinstance(data.get("routes"), list):
            raise ApiError(502, "ROUTING_PROVIDER_ERROR", "A valid route could not be retrieved. No route assessment was created.", {})
        if not data["routes"]:
            return []
        retrieved = utcnow()
        notes = ["The routing provider does not account for departure time or live traffic; "
                 "duration is a typical-conditions estimate."]
        records, rejected = [], 0
        for raw in data["routes"]:
            try:
                records.append(build_route_record(
                    distance=raw.get("distance"), duration=raw.get("duration"), geometry=raw.get("geometry"),
                    provider=self.name, retrieved_at=retrieved, notes=notes))
            except (ValueError, AttributeError):
                rejected += 1
        if not records:
            raise ApiError(502, "ROUTING_PROVIDER_ERROR", "A valid route could not be retrieved. No route assessment was created.", {})
        return records

    def probe(self) -> Tuple[bool, str]:
        try:
            status, data = self.http.request_json(
                "GET", f"{self.settings.routing_base_url}/nearest/v1/driving/77.2090,28.6139", timeout=5.0)
        except UpstreamTimeout:
            return False, "timed out"
        except UpstreamError:
            return False, "network error"
        ok = status == 200 and isinstance(data, dict) and data.get("code") == "Ok"
        return ok, "reachable" if ok else f"HTTP {status}"
