from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.observability import ApplicationMetrics, reset_request_id, set_request_id

logger = logging.getLogger(__name__)


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
