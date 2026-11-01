"""Weather source abstraction (F6/F7). Providers are replaceable; unavailable != no risk (L-A3)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from backend.models.records import WeatherRecord, iso, utcnow

# status values: connected | partial | unavailable | not_configured


@dataclass
class WeatherSnapshot:
    status: str
    source_name: str
    retrieved_at: Optional[datetime]
    records: List[WeatherRecord] = field(default_factory=list)
    covered_district_ids: Set[str] = field(default_factory=set)  # districts the source answered for
    errors: List[str] = field(default_factory=list)
    feeds: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status in ("connected", "partial")

    def summary(self) -> Dict[str, Any]:
        return {
            "status": self.status, "source_name": self.source_name,
            "retrieved_at": iso(self.retrieved_at), "record_count": len(self.records),
            "districts_covered": sorted(self.covered_district_ids),
            "errors": list(self.errors), "feeds": list(self.feeds),
        }


class WeatherService:
    """Caches the provider snapshot (TTL) and exposes refresh()."""

    def __init__(self, provider: Any, ttl_seconds: int = 600):
        self.provider = provider
        self.ttl = ttl_seconds
        self._snap: Optional[WeatherSnapshot] = None
        self._fetched_mono: Optional[float] = None
        self._lock = threading.Lock()

    def get_snapshot(self, force: bool = False) -> WeatherSnapshot:
        import time
        with self._lock:
            fresh = self._snap is not None and self._fetched_mono is not None and (time.monotonic() - self._fetched_mono) < self.ttl
            if force or not fresh:
                self._snap = self.provider.fetch()
                self._fetched_mono = time.monotonic()
            return self._snap

    def status(self) -> Dict[str, Any]:
        return self.provider.status()
