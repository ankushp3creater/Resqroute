"""Application state: wires settings, providers, dataset and store together (replaceable in tests)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import Settings
from backend.http import HttpClient
from backend.models.records import iso, utcnow
from backend.services.ai.gemini_client import GeminiClient
from backend.services.geography.loader import DistrictDataset, GeographyUnavailable, load_dataset
from backend.services.routing.geocoder import Geocoder
from backend.services.routing.osrm import OsrmProvider
from backend.services.store import IdempotencyStore, Store
from backend.services.weather.base import WeatherService
from backend.services.weather.imd.client import ImdProvider


@dataclass
class AppState:
    settings: Settings
    geocoder: Any
    router: Any
    weather: WeatherService
    gemini: GeminiClient
    store: Store = field(default_factory=Store)
    idempotency: IdempotencyStore = field(default_factory=IdempotencyStore)
    dataset: Optional[DistrictDataset] = None
    dataset_error: Optional[str] = None
    _probe_cache: Dict[str, Any] = field(default_factory=dict)

    def reload_dataset(self) -> None:
        try:
            self.dataset, self.dataset_error = load_dataset(Path(self.settings.boundary_geojson_path)), None
        except GeographyUnavailable as exc:
            self.dataset, self.dataset_error = None, str(exc)

    # ---- health (F1, AC-A1/B1): statuses only; credentials are never included ----
    def _cached(self, key: str, ttl: float, fn):
        hit = self._probe_cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._probe_cache[key] = (time.monotonic(), val)
        return val

    def health(self) -> Dict[str, Any]:
        now = iso(utcnow())
        ok, detail = self._cached("routing", 120, self.router.probe)
        routing = {"status": "connected" if ok else "unavailable", "detail": detail, "provider": self.router.name}
        ok, detail = self._cached("geocoding", 120, self.geocoder.probe)
        geocoding = {"status": "connected" if ok else "unavailable", "detail": detail, "provider": self.geocoder.name}
        imd = self.weather.status()
        gstatus, gdetail = self._cached("gemini", 300, self.gemini.probe)
        gemini = {"status": gstatus, "detail": gdetail}
        if self.dataset is None:
            geography = {"status": "unavailable", "detail": self.dataset_error or "dataset not loaded"}
        else:
            md = self.dataset.metadata
            geography = {"status": "partially_available" if md["coverage_status"] == "partial" else "connected",
                         "detail": f"{md['available_count']} of {md['expected_count']} districts available",
                         "available_count": md["available_count"], "expected_count": md["expected_count"]}
        return {"status": "ok", "integrations": {"routing": routing, "geocoding": geocoding, "imd": imd,
                                                  "gemini": gemini, "geography": geography},
                "checked_at": now, "timestamp": now}


def build_state(settings: Optional[Settings] = None, *, http: Any = None, geocoder: Any = None, router: Any = None,
                weather_provider: Any = None, gemini: Any = None) -> AppState:
    settings = settings or Settings.from_env()
    http = http or HttpClient()
    state = AppState(
        settings=settings,
        geocoder=geocoder or Geocoder(settings, http),
        router=router or OsrmProvider(settings, http),
        weather=WeatherService(weather_provider or ImdProvider(settings, http), settings.weather_cache_ttl_seconds),
        gemini=gemini or GeminiClient(settings, http),
    )
    state.reload_dataset()
    return state
