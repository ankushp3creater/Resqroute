"""IMD weather adapter - extension point (F6).

Current state (honest): NOT CONNECTED. The official IMD endpoints, access method and response schemas
have not been verified, so no feed is registered and nothing is fetched or invented.

To integrate IMD later:
  1. Verify the endpoint + auth in the official docs (https://api.imd.gov.in/), set IMD_API_BASE_URL
     (and IMD_API_KEY if required) in .env.
  2. Fill severity_maps.SEVERITY_MAPS and district_crosswalk.CROSSWALK from official documentation.
  3. Write a parser `parse(payload, retrieved_at) -> (records, covered_district_ids)` that builds
     WeatherRecord objects (call .validate()) and register it below with ImdFeed(...).
Nothing else in the application needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.config import Settings
from backend.http import HttpClient, UpstreamError, UpstreamTimeout
from backend.models.records import WeatherRecord, utcnow
from backend.services.weather.base import WeatherSnapshot

Parser = Callable[[Any, datetime], Tuple[List[WeatherRecord], Set[str]]]


@dataclass
class ImdFeed:
    name: str            # e.g. "district_warning" | "district_nowcast" | "rainfall"
    path: str            # path under IMD_API_BASE_URL (verified!)
    parser: Parser
    params: Optional[Dict[str, Any]] = None


FEEDS: List[ImdFeed] = []   # <- register verified feeds here


class ImdProvider:
    name = "IMD"

    def __init__(self, settings: Settings, http: Any = None, feeds: Optional[List[ImdFeed]] = None):
        self.settings = settings
        self.http = http or HttpClient()
        self.feeds = FEEDS if feeds is None else feeds

    @property
    def configured(self) -> bool:
        return bool(self.settings.imd_api_base_url)

    def status(self) -> Dict[str, str]:
        if not self.configured:
            return {"status": "not_configured",
                    "detail": "IMD integration is not configured. Weather risk cannot be assessed."}
        if not self.feeds:
            return {"status": "unavailable",
                    "detail": "IMD base URL is set but no verified feed adapter is registered yet."}
        return {"status": "connected", "detail": f"{len(self.feeds)} feed adapter(s) registered."}

    def fetch(self) -> WeatherSnapshot:
        now = utcnow()
        if not self.configured:
            return WeatherSnapshot("not_configured", self.name, None,
                                   errors=["IMD integration is not configured; no official weather data was retrieved."])
        if not self.feeds:
            return WeatherSnapshot("unavailable", self.name, None,
                                   errors=["No verified IMD feed adapter is registered; no official weather data was retrieved."])
        records: List[WeatherRecord] = []
        covered: Set[str] = set()
        errors: List[str] = []
        feed_status: List[Dict[str, Any]] = []
        ok = 0
        for feed in self.feeds:
            headers = {"Authorization": f"Bearer {self.settings.imd_api_key}"} if self.settings.imd_api_key else None
            try:
                status, payload = self.http.request_json(
                    "GET", f"{self.settings.imd_api_base_url}{feed.path}", params=feed.params, headers=headers,
                    timeout=self.settings.request_timeout_seconds)
                if status != 200 or payload is None:
                    raise UpstreamError(status, f"HTTP {status}")
                recs, cov = feed.parser(payload, now)
                valid = [r.validate() for r in recs]
                records.extend(valid)
                covered |= set(cov)
                ok += 1
                feed_status.append({"name": feed.name, "status": "connected", "retrieved_at": now.isoformat(),
                                    "record_count": len(valid), "error": None})
            except UpstreamTimeout:
                errors.append(f"IMD feed '{feed.name}' timed out.")
                feed_status.append({"name": feed.name, "status": "unavailable", "retrieved_at": None, "record_count": 0, "error": "timeout"})
            except (UpstreamError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"IMD feed '{feed.name}' returned an invalid or failed response.")
                feed_status.append({"name": feed.name, "status": "unavailable", "retrieved_at": None, "record_count": 0,
                                    "error": type(exc).__name__})
        status = "connected" if ok == len(self.feeds) else ("partial" if ok else "unavailable")
        return WeatherSnapshot(status, self.name, now if ok else None, records, covered, errors, feed_status)
