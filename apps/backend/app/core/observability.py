from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_MONGODB_URI = re.compile(r"mongodb(?:\+srv)?://[^\s\"']+", re.IGNORECASE)
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_TOKEN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|jwt[_-]?secret|password|token)"
    r"\s*[:=]\s*[^\s,;]+"
)


def redact(value: str) -> str:
    redacted = _MONGODB_URI.sub("[REDACTED_MONGODB_URI]", value)
    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)
    redacted = _JWT_TOKEN.sub("[REDACTED_JWT]", redacted)
    redacted = _EMAIL.sub("[REDACTED_EMAIL]", redacted)
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    """Return the active request/trace identifier.

    This is the project's single correlation concept. Background tasks rebind it
    with :func:`set_request_id` so one logical request stays traceable from the
    HTTP layer through the outbox and into the worker.
    """
    return _request_id.get()


class JsonLogFormatter(logging.Formatter):
    _extra_fields = {
        "attempt": "attempt",
        "correlation_id": "correlationId",
        "duration_ms": "durationMs",
        "error_type": "errorType",
        "event_id": "eventId",
        "event_type": "eventType",
        "http_method": "httpMethod",
        "http_route": "httpRoute",
        "http_status": "httpStatus",
        "input_tokens": "inputTokens",
        "line_operation": "lineOperation",
        "model": "model",
        "order_id": "orderId",
        "order_status": "orderStatus",
        "output_tokens": "outputTokens",
        "queue_name": "queueName",
        "task_id": "taskId",
        "task_name": "taskName",
        "upstream_status": "upstreamStatus",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
        }
        request_id = _request_id.get()
        if request_id:
            payload["requestId"] = request_id
        for source, target in self._extra_fields.items():
            value = getattr(record, source, None)
            if value is not None:
                payload[target] = redact(value) if isinstance(value, str) else value
        if record.exc_info and record.exc_info[0]:
            payload["errorType"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SafeTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(*, level: str, json_logs: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            SafeTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for logger_name in ("uvicorn", "uvicorn.error", "fastapi"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


class ApplicationMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "food_ordering_http_requests_total",
            "HTTP requests handled by the API.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "food_ordering_http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ("method", "route"),
            registry=self.registry,
        )
        self.orders_created = Counter(
            "food_ordering_orders_created_total",
            "Orders created after idempotency checks.",
            registry=self.registry,
        )
        self.order_value = Counter(
            "food_ordering_order_value_total",
            "Server-calculated value of created orders.",
            registry=self.registry,
        )
        self.order_status_changes = Counter(
            "food_ordering_order_status_changes_total",
            "Successful order status changes.",
            ("status",),
            registry=self.registry,
        )
        self.recommendations_served = Counter(
            "food_ordering_recommendations_served_total",
            "Recommendation responses by selected strategy.",
            ("strategy",),
            registry=self.registry,
        )
        self.recommendation_events = Counter(
            "food_ordering_recommendation_events_total",
            "Accepted recommendation events by event type.",
            ("event_type",),
            registry=self.registry,
        )
        self.recommendation_latency = Histogram(
            "food_ordering_recommendation_duration_seconds",
            "Recommendation serving latency by bounded strategy.",
            ("strategy",),
            registry=self.registry,
        )
        self.recommendation_fallbacks = Counter(
            "food_ordering_recommendation_fallbacks_total",
            "Recommendation fallbacks by bounded reason.",
            ("reason",),
            registry=self.registry,
        )
        self.recommendation_cache = Counter(
            "food_ordering_recommendation_cache_total",
            "Recommendation artifact and result cache outcomes.",
            ("cache", "outcome"),
            registry=self.registry,
        )
        self.recommendation_model_age = Gauge(
            "food_ordering_recommendation_active_model_age_seconds",
            "Age of the active local recommendation model.",
            registry=self.registry,
        )
        self.llm_latency = Histogram(
            "food_ordering_llm_request_duration_seconds",
            "Customer-agent LLM latency in seconds.",
            ("model",),
            registry=self.registry,
        )
        self.llm_errors = Counter(
            "food_ordering_llm_errors_total",
            "Customer-agent LLM request failures.",
            ("model",),
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "food_ordering_llm_tokens_total",
            "Reported LLM token usage.",
            ("model", "direction"),
            registry=self.registry,
        )
        self.llm_estimated_cost = Counter(
            "food_ordering_llm_estimated_cost_usd_total",
            "Estimated LLM cost in USD from configured per-million-token prices.",
            ("model",),
            registry=self.registry,
        )

    def observe_http(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        self.http_requests.labels(method, route, str(status_code)).inc()
        self.http_latency.labels(method, route).observe(duration_seconds)

    def record_order_created(self, total: Decimal) -> None:
        self.orders_created.inc()
        self.order_value.inc(float(total))

    def record_order_status(self, status: str) -> None:
        self.order_status_changes.labels(status).inc()

    def record_recommendations_served(self, strategy: str) -> None:
        self.recommendations_served.labels(strategy).inc()

    def record_recommendation_event(self, event_type: str) -> None:
        self.recommendation_events.labels(event_type).inc()

    def observe_recommendation(self, *, strategy: str, duration_seconds: float) -> None:
        self.recommendation_latency.labels(strategy).observe(duration_seconds)

    def record_recommendation_fallback(self, reason: str) -> None:
        self.recommendation_fallbacks.labels(reason).inc()

    def record_recommendation_cache(self, *, cache: str, outcome: str) -> None:
        self.recommendation_cache.labels(cache, outcome).inc()

    def set_recommendation_model_age(self, age_seconds: float) -> None:
        self.recommendation_model_age.set(max(age_seconds, 0))

    def observe_llm(
        self,
        *,
        model: str,
        duration_seconds: float,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        failed: bool,
    ) -> None:
        self.llm_latency.labels(model).observe(duration_seconds)
        if failed:
            self.llm_errors.labels(model).inc()
        if input_tokens:
            self.llm_tokens.labels(model, "input").inc(input_tokens)
        if output_tokens:
            self.llm_tokens.labels(model, "output").inc(output_tokens)
        if estimated_cost_usd:
            self.llm_estimated_cost.labels(model).inc(estimated_cost_usd)

    def render(self) -> bytes:
        return generate_latest(self.registry)
