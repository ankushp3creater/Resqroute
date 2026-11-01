"""Configuration. Reads environment variables (and an optional .env file).

No secret is ever hardcoded. Empty values are treated as "not set".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value.strip() if value and value.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    app_env: str = "development"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    routing_base_url: str = "https://router.project-osrm.org"
    routing_api_key: str = ""  # unused by OSRM; kept for providers that need one
    routing_alternatives: int = 3
    geocoder_base_url: str = "https://nominatim.openstreetmap.org"
    geocoder_user_agent: str = "ResQRoute-Hackathon/0.1 (set GEOCODER_USER_AGENT with a contact)"
    geocoder_country_codes: str = "in"
    imd_api_base_url: str = ""
    imd_api_key: str = ""
    cors_allowed_origins: List[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    request_timeout_seconds: float = 15.0
    ai_timeout_seconds: float = 60.0
    agent_max_steps: int = 6
    max_body_bytes: int = 64 * 1024
    past_departure_grace_seconds: int = 300
    weather_cache_ttl_seconds: int = 600
    weather_max_age_seconds: int = 6 * 3600
    boundary_geojson_path: Path = BACKEND_DIR / "data" / "haryana_district_boundaries.geojson"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(ROOT_DIR / ".env")
        _load_dotenv(BACKEND_DIR / ".env")
        origins = [o.strip() for o in _env("CORS_ALLOWED_ORIGINS").split(",") if o.strip()]
        return cls(
            app_env=_env("APP_ENV", "development"),
            gemini_api_key=_env("GEMINI_API_KEY"),
            gemini_model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_base_url=_env("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
            routing_base_url=_env("ROUTING_API_BASE_URL", "https://router.project-osrm.org").rstrip("/"),
            routing_api_key=_env("ROUTING_API_KEY"),
            routing_alternatives=max(0, min(5, _env_int("ROUTING_ALTERNATIVES", 3))),
            geocoder_base_url=_env("GEOCODER_API_BASE_URL", "https://nominatim.openstreetmap.org").rstrip("/"),
            geocoder_user_agent=_env("GEOCODER_USER_AGENT", cls.geocoder_user_agent),
            geocoder_country_codes=_env("GEOCODER_COUNTRY_CODES", "in"),
            imd_api_base_url=_env("IMD_API_BASE_URL").rstrip("/"),
            imd_api_key=_env("IMD_API_KEY"),
            cors_allowed_origins=origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
            request_timeout_seconds=_env_float("REQUEST_TIMEOUT_SECONDS", 15.0),
            ai_timeout_seconds=_env_float("AI_TIMEOUT_SECONDS", 60.0),
        )
