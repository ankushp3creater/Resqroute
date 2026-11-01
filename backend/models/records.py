"""Domain constants and record types (spec 5.3, 5.6). Pure Python, no third-party imports."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

HAZARD_TYPES = (
    "heavy_rainfall", "extremely_heavy_rainfall", "thunderstorm", "lightning",
    "strong_wind", "heatwave", "cold_wave", "fog", "other", "unknown",
)
SEVERITIES = ("green", "yellow", "orange", "red", "unknown")
RISK_LEVELS = SEVERITIES
COVERAGE_STATUSES = ("complete", "partial", "unavailable")
SEVERITY_RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
IST = timezone(timedelta(hours=5, minutes=30))

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat()


def clean_text(value: Any, max_len: int = 200) -> str:
    """Strip control characters and collapse whitespace. All external/user text goes through this."""
    if value is None:
        return ""
    return " ".join(_CONTROL.sub(" ", str(value)).split())[:max_len]


def _aware_or_none(dt: Any, name: str) -> None:
    if dt is not None and (not isinstance(dt, datetime) or dt.tzinfo is None):
        raise ValueError(f"{name} must be a timezone-aware datetime or None")


@dataclass(frozen=True)
class WeatherRecord:
    record_id: str
    district_id: Optional[str]
    hazard_type: str
    severity: str
    observation_value: Optional[float]
    observation_unit: Optional[str]
    issued_at: Optional[datetime]
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    source_name: str
    source_record_id: Optional[str]
    retrieved_at: datetime
    feed: Optional[str] = None           # feed name: severity maps differ per feed (L-A1)
    original_value: Optional[str] = None  # original source value, preserved (5.3)
    text: Optional[str] = None            # untrusted free text from the source

    def validate(self) -> "WeatherRecord":
        if not isinstance(self.record_id, str) or not self.record_id:
            raise ValueError("record_id is required")
        if self.hazard_type not in HAZARD_TYPES:
            raise ValueError(f"invalid hazard_type: {self.hazard_type!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"invalid severity: {self.severity!r}")
        if self.observation_value is not None and (
            isinstance(self.observation_value, bool)
            or not isinstance(self.observation_value, (int, float))
            or not math.isfinite(self.observation_value)
        ):
            raise ValueError("observation_value must be a finite number or None")
        for name in ("issued_at", "valid_from", "valid_until"):
            _aware_or_none(getattr(self, name), name)
        if not isinstance(self.retrieved_at, datetime) or self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be a timezone-aware datetime")
        if not self.source_name:
            raise ValueError("source_name is required")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until precedes valid_from")
        return self

    @property
    def evidence_ref(self) -> str:
        return f"wx:{self.record_id}"

    def to_dict(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        d = {
            "record_id": self.record_id, "evidence_ref": self.evidence_ref,
            "district_id": self.district_id, "hazard_type": self.hazard_type,
            "severity": self.severity, "observation_value": self.observation_value,
            "observation_unit": self.observation_unit, "issued_at": iso(self.issued_at),
            "valid_from": iso(self.valid_from), "valid_until": iso(self.valid_until),
            "source_name": self.source_name, "source_record_id": self.source_record_id,
            "retrieved_at": iso(self.retrieved_at), "feed": self.feed,
            "original_value": self.original_value,
            "text": clean_text(self.text, 500) if self.text else None,
        }
        if now is not None:
            d["display_state"] = display_state(self, now)
        return d


def temporal_status(rec: WeatherRecord, window_start: datetime, window_end: datetime) -> str:
    """L-B5: applicable iff valid_from <= travel_end AND valid_until >= departure."""
    if rec.valid_from is None or rec.valid_until is None:
        return "uncertain"
    if rec.valid_until < window_start:
        return "expired"
    if rec.valid_from > window_end:
        return "future"
    return "applicable"


def display_state(rec: WeatherRecord, now: datetime) -> str:
    """F7: distinguish expired / current / no-warning / uncertain records."""
    if rec.valid_from is None or rec.valid_until is None:
        return "uncertain"
    if rec.valid_until < now:
        return "expired"
    if rec.valid_from > now:
        return "upcoming"
    return "no_warning" if rec.severity == "green" else "current"
