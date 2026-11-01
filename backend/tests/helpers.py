"""TEST-ONLY helpers. Everything here is synthetic and must NEVER be imported by application code."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from backend.models.records import WeatherRecord
from backend.services.weather.base import WeatherSnapshot

UTC = timezone.utc
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@contextmanager
def raises(exc_type, code: Optional[str] = None):
    try:
        yield
    except exc_type as e:
        if code is not None:
            assert getattr(e, "code", None) == code, f"expected code {code}, got {getattr(e, 'code', None)}"
        return
    raise AssertionError(f"{exc_type.__name__} was not raised")


class FakeHttp:
    """Scripted HTTP transport (TEST ONLY). Each call pops the next (status, body) or raises it."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def request_json(self, method, url, **kw):
        self.calls.append((method, url, kw))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_record(rid="TEST-1", district="HR-058", severity="orange", hazard="heavy_rainfall",
                start_h=0, end_h=6, base=NOW, valid=True) -> WeatherRecord:
    return WeatherRecord(
        record_id=rid, district_id=district, hazard_type=hazard, severity=severity,
        observation_value=None, observation_unit=None, issued_at=base,
        valid_from=(base + timedelta(hours=start_h)) if valid else None,
        valid_until=(base + timedelta(hours=end_h)) if valid else None,
        source_name="TEST-SOURCE", source_record_id=rid, retrieved_at=base, feed="test_feed").validate()


def snapshot(records: List[WeatherRecord], covered: Tuple[str, ...], status="connected", base=NOW) -> WeatherSnapshot:
    return WeatherSnapshot(status, "TEST-SOURCE", base, records, set(covered), [], [])


class FixedWeatherProvider:
    """Test-only provider returning a fixed snapshot."""
    name = "TEST-SOURCE"

    def __init__(self, snap): self.snap = snap
    def fetch(self): return self.snap
    def status(self): return {"status": self.snap.status, "detail": "test"}


def gemini_text(text: str):
    return (200, {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})


def gemini_call(name: str, args: dict):
    return (200, {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}}]}}]})
