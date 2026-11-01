"""The ONLY module that talks to Gemini (spec 6: one backend AI service module).

Uses the public REST API so there is no SDK-version risk. The key is sent in a header and never logged.
Model name comes from GEMINI_MODEL (default is a guess that must be checked against your account).
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from backend.config import Settings
from backend.http import HttpClient, UpstreamError, UpstreamTimeout


class AiError(Exception):
    code = "AI_SERVICE_ERROR"
    http_status = 502

    def __init__(self, message: str = "AI analysis could not be completed.", tool_trace=None):
        super().__init__(message)
        self.message = message
        self.tool_trace = tool_trace or []


class AiNotConfigured(AiError):
    code, http_status = "AI_NOT_CONFIGURED", 503


class AiTimeout(AiError):
    code, http_status = "AI_TIMEOUT", 504


class AiServiceError(AiError):
    code, http_status = "AI_SERVICE_ERROR", 502


class InvalidAiResponse(AiError):
    code, http_status = "INVALID_AI_RESPONSE", 502


class GeminiClient:
    def __init__(self, settings: Settings, http: Any = None):
        self.settings = settings
        self.http = http or HttpClient()

    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    @property
    def model(self) -> str:
        return self.settings.gemini_model

    def _headers(self) -> Dict[str, str]:
        return {"x-goog-api-key": self.settings.gemini_api_key}

    def generate(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            raise AiNotConfigured("AI analysis is not configured. Deterministic route-risk results are still available.")
        url = f"{self.settings.gemini_base_url}/models/{self.settings.gemini_model}:generateContent"
        try:
            status, data = self.http.request_json("POST", url, headers=self._headers(), json_body=body,
                                                  timeout=self.settings.ai_timeout_seconds)
        except UpstreamTimeout:
            raise AiTimeout("AI analysis timed out. Your deterministic risk assessment is available.")
        except UpstreamError:
            raise AiServiceError("AI analysis could not be completed. Your deterministic risk assessment is available.")
        if status == 429:
            raise AiServiceError("The AI service is rate-limited right now. Please retry shortly.")
        if status in (400, 401, 403):
            raise AiServiceError(f"The AI service rejected the request (HTTP {status}). Check the API key and model name.")
        if status == 404:
            raise AiServiceError("The configured Gemini model was not found. Check GEMINI_MODEL.")
        if status != 200 or not isinstance(data, dict):
            raise AiServiceError(f"The AI service returned an unexpected response (HTTP {status}).")
        return data

    def probe(self) -> Tuple[str, str]:
        """Returns (status, detail) without spending tokens: reads model metadata."""
        if not self.configured:
            return "not_configured", "GEMINI_API_KEY is not set. AI analysis is unavailable."
        url = f"{self.settings.gemini_base_url}/models/{self.settings.gemini_model}"
        try:
            status, _ = self.http.request_json("GET", url, headers=self._headers(), timeout=5.0)
        except UpstreamTimeout:
            return "unavailable", "Gemini did not respond in time."
        except UpstreamError:
            return "unavailable", "Gemini could not be reached."
        if status == 200:
            return "connected", f"Model '{self.settings.gemini_model}' is reachable."
        if status in (400, 401, 403):
            return "unavailable", "Gemini rejected the API key."
        if status == 404:
            return "unavailable", "Configured Gemini model was not found."
        return "unavailable", f"Gemini returned HTTP {status}."
