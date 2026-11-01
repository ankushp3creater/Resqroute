"""Minimal JSON-over-HTTP client (stdlib only) so external calls are easy to fake in tests."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

MAX_RESPONSE_BYTES = 12 * 1024 * 1024


class UpstreamTimeout(Exception):
    pass


class UpstreamError(Exception):
    def __init__(self, status: Optional[int], safe_message: str):
        super().__init__(safe_message)
        self.status = status
        self.safe_message = safe_message


class HttpClient:
    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        timeout: float = 15.0,
    ) -> Tuple[int, Any]:
        """Returns (status, parsed_json_or_None). Non-2xx statuses are returned, not raised."""
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        hdrs = {"Accept": "application/json"}
        hdrs.update(headers or {})
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                body = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read(MAX_RESPONSE_BYTES + 1)
            except Exception:  # pragma: no cover
                body = b""
        except (socket.timeout, TimeoutError):
            raise UpstreamTimeout()
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise UpstreamTimeout()
            raise UpstreamError(None, "network error")
        if len(body) > MAX_RESPONSE_BYTES:
            raise UpstreamError(status, "response too large")
        if not body:
            return status, None
        try:
            return status, json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return status, None
