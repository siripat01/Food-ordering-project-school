from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from typing import Any, TypeVar, cast

from taskiq import Context

from app.bootstrap import WorkerServices
from app.core.observability import set_request_id

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def services_from(context: Context) -> WorkerServices:
    """Return dependencies built once for this Taskiq worker process."""

    services = getattr(context.state, "services", None)
    if services is None:
        raise RuntimeError("Taskiq worker services are not initialized")
    return cast(WorkerServices, services)


def bind_correlation_id(correlation_id: str | None, *, task_id: str) -> None:
    """Continue the caller's trace inside the worker.

    Falls back to the Taskiq task id so a task started without a correlation id
    is still traceable in the structured logs.
    """

    set_request_id(correlation_id or task_id)


def track_task(task_name: str) -> Callable[[F], F]:
    """Record bounded task outcomes using metrics attached to worker services."""

    def decorator(function: F) -> F:
        @wraps(function)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            started_at = perf_counter()
            try:
                result = await function(*args, **kwargs)
            except Exception:
                _record_task_metric(kwargs.get("context"), task_name, "failed", started_at)
                raise
            else:
                _record_task_metric(kwargs.get("context"), task_name, "success", started_at)
                return result

        return cast(F, wrapped)

    return decorator


def _record_task_metric(
    context: Any,
    task_name: str,
    outcome: str,
    started_at: float,
) -> None:
    services = getattr(getattr(context, "state", None), "services", None)
    metrics = getattr(services, "metrics", None)
    if metrics is not None:
        metrics.record_task(task_name, outcome, perf_counter() - started_at)
