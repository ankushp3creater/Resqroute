"""Geocoding via Nominatim (swappable). Resolves place text to coordinates (E2)."""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, Tuple

from backend.config import Settings
from backend.errors import ApiError
from backend.http import HttpClient, UpstreamError, UpstreamTimeout
from backend.models.records import clean_text


class Geocoder:
    name = "Nominatim (OpenStreetMap)"
    MIN_INTERVAL_S = 1.1  # Nominatim usage policy: max 1 request/second

    def __init__(self, settings: Settings, http: Any = None, sleep=time.sleep, clock=time.monotonic):
        self.settings = settings
        self.http = http or HttpClient()
        self._sleep, self._clock = sleep, clock
        self._last = None
        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}

    def resolve(self, loc: Dict[str, Any], field: str = "location") -> Dict[str, Any]:
        if loc.get("latitude") is not None:
            return {"latitude": loc["latitude"], "longitude": loc["longitude"],
                    "label": loc.get("text") or "Coordinates supplied by user", "source": "user_coordinates"}
        text = loc["text"]
        key = text.casefold()
        with self._lock:
            if key in self._cache:
                return dict(self._cache[key])
            if self._last is not None:
                wait = self.MIN_INTERVAL_S - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            params = {"q": text, "format": "jsonv2", "limit": 1}
            if self.settings.geocoder_country_codes:
                params["countrycodes"] = self.settings.geocoder_country_codes
            try:
                status, data = self.http.request_json(
                    "GET", f"{self.settings.geocoder_base_url}/search", params=params,
                    headers={"User-Agent": self.settings.geocoder_user_agent},
                    timeout=self.settings.request_timeout_seconds)
            except UpstreamTimeout:
                raise ApiError(504, "GEOCODER_TIMEOUT", "The location search service did not respond. Please try again.", {"field": field})
            except UpstreamError:
                raise ApiError(502, "GEOCODER_ERROR", "The location search service is unavailable. Please try again.", {"field": field})
            finally:
                self._last = self._clock()
            if status != 200 or not isinstance(data, list):
                raise ApiError(502, "GEOCODER_ERROR", "The location search service returned an unexpected response.", {"field": field})
            if not data:
                raise ApiError(422, "LOCATION_NOT_FOUND",
                               "We couldn't identify one of these locations. Try a more specific place name.",
                               {"field": field})
            try:
                hit = data[0]
                lat, lon = float(hit["lat"]), float(hit["lon"])
                if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ApiError(502, "GEOCODER_ERROR", "The location search service returned an invalid location.", {"field": field})
            result = {"latitude": lat, "longitude": lon,
                      "label": clean_text(hit.get("display_name") or text, 200), "source": "nominatim"}
            self._cache[key] = result
            return dict(result)

    def probe(self) -> Tuple[bool, str]:
        try:
            status, _ = self.http.request_json(
                "GET", f"{self.settings.geocoder_base_url}/status.php", params={"format": "json"},
                headers={"User-Agent": self.settings.geocoder_user_agent}, timeout=5.0)
        except UpstreamTimeout:
            return False, "timed out"
        except UpstreamError:
            return False, "network error"
        return (status == 200), ("reachable" if status == 200 else f"HTTP {status}")
