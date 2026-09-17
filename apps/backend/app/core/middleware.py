from __future__ import annotations

import logging
import re
from collections.abc import Collection
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.observability import ApplicationMetrics, reset_request_id, set_request_id

logger = logging.getLogger(__name__)

CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AUTH_COOKIE_NAMES = frozenset({"access_token", "refresh_token"})


def _origin_from_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def is_trusted_cookie_request(
    *,
    method: str,
    cookies: Collection[str] | dict[str, str],
    origin: str | None,
    referer: str | None,
    allowed_origins: Collection[str],
) -> bool:
    """Allow safe requests and cookie mutations from configured browser origins."""
    if method.upper() in CSRF_SAFE_METHODS:
        return True
    if not AUTH_COOKIE_NAMES.intersection(cookies):
        return True
    candidate = _origin_from_url(origin) or _origin_from_url(referer)
    trusted = {_origin_from_url(value) for value in allowed_origins}
    return candidate is not None and candidate in trusted


class CookieCSRFMiddleware:
    """Reject cross-site state-changing requests authenticated by cookies."""

    def __init__(self, app: ASGIApp, *, allowed_origins: Collection[str]) -> None:
        self.app = app
        self.allowed_origins = tuple(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if not is_trusted_cookie_request(
            method=request.method,
            cookies=request.cookies,
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            allowed_origins=self.allowed_origins,
        ):
            response = JSONResponse(
                {"detail": "CSRF validation failed"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp, metrics: ApplicationMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        candidate = Headers(scope=scope).get("X-Request-ID", "")
        request_id = (
            candidate if re.fullmatch(r"[A-Za-z0-9._-]{1,100}", candidate) else str(uuid4())
        )
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        request_token = set_request_id(request_id)
        started_at = perf_counter()
        status_code = 500
        error_type: str | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            duration_seconds = perf_counter() - started_at
            route = getattr(scope.get("route"), "path", "unmatched")
            method = str(scope.get("method", "UNKNOWN"))
            self.metrics.observe_http(
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            log_method = logger.error if error_type else logger.info
            log_method(
                "http_request_completed",
                extra={
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "error_type": error_type,
                    "http_method": method,
                    "http_route": route,
                    "http_status": status_code,
                },
            )
            reset_request_id(request_token)
