from __future__ import annotations

from typing import cast

from taskiq import Context

from app.core.container import ServiceContainer
from app.core.observability import set_request_id


def container_from(context: Context) -> ServiceContainer:
    """Return the service container built once per worker process."""
    container = getattr(context.state, "container", None)
    if container is None:
        raise RuntimeError("Taskiq worker container is not initialized")
    return cast(ServiceContainer, container)


def bind_correlation_id(correlation_id: str | None, *, task_id: str) -> None:
    """Continue the caller's trace inside the worker.

    Falls back to the Taskiq task id so a task started without a correlation id
    is still traceable in the structured logs.
    """
    set_request_id(correlation_id or task_id)
