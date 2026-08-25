from __future__ import annotations

from typing import cast

from taskiq import Context

from app.bootstrap import WorkerServices
from app.core.observability import set_request_id


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
