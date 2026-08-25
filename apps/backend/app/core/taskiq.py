from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

from taskiq import TaskiqEvents, TaskiqState
from taskiq.middlewares import SimpleRetryMiddleware
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "food-ordering"
RESULT_TTL_SECONDS = 3600
TASK_RETRY_COUNT = 3

#: Modules that register task handlers. They are imported at the bottom of this
#: module so that ``taskiq worker app.core.taskiq:broker`` registers every
#: handler without extra discovery flags.
TASK_MODULES = (
    "app.jobs.order",
    "app.jobs.line",
    "app.jobs.agent",
)

_settings = get_settings()

# Taskiq owns its own Redis connections. The application Redis client
# (app.db.redis.RedisDatabase) stays separate because it serves business
# caching and auth session state, not broker internals.
result_backend: RedisAsyncResultBackend[Any] = RedisAsyncResultBackend(
    redis_url=_settings.redis_url,
    result_ex_time=RESULT_TTL_SECONDS,
)

broker = (
    RedisStreamBroker(url=_settings.redis_url, queue_name=QUEUE_NAME)
    .with_result_backend(result_backend)
    .with_middlewares(SimpleRetryMiddleware(default_retry_count=TASK_RETRY_COUNT))
)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def on_worker_startup(state: TaskiqState) -> None:
    """Build the worker's service container once per worker process.

    The worker is a separate process from the API, so it has to install the
    project's structured logging itself.
    """
    from app.core.container import ServiceContainer
    from app.core.observability import configure_logging

    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    container = ServiceContainer(settings)
    await container.start()
    state.container = container
    logger.info("taskiq_worker_started", extra={"queue_name": QUEUE_NAME})


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_worker_shutdown(state: TaskiqState) -> None:
    container = getattr(state, "container", None)
    if container is not None:
        await container.close()
    logger.info("taskiq_worker_stopped", extra={"queue_name": QUEUE_NAME})


def import_task_modules() -> None:
    """Import every task module so its handlers register on the broker."""
    for module in TASK_MODULES:
        import_module(module)


import_task_modules()
