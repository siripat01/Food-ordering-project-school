from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    admin,
    auth,
    health,
    line,
    metrics,
    orders,
    products,
    recommendations,
    staff,
)
from app.core.config import Settings, get_settings
from app.core.container import ServiceContainer
from app.core.middleware import RequestIDMiddleware
from app.core.observability import ApplicationMetrics, configure_logging
from app.core.taskiq import broker
from app.domain.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)


def create_app(settings: Settings | None = None, *, initialize_clients: bool = True) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(level=resolved_settings.log_level, json_logs=resolved_settings.log_json)
    application_metrics = ApplicationMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not initialize_clients:
            yield
            return
        container = ServiceContainer(resolved_settings, metrics=application_metrics)
        try:
            await container.start()
            # The API is a Taskiq *client*: it enqueues work but never consumes
            # it. The worker and the outbox dispatcher run as separate processes
            # so no polling loop is started per Uvicorn worker.
            await broker.startup()
            app.state.container = container
            app.state.db = container.db
            app.state.redis = container.redis.client
            app.state.http_client = container.http_client
            app.state.users = container.users
            app.state.auth_sessions = container.auth_sessions
            app.state.products = container.products
            app.state.recommendation_runtime = container.recommendation_runtime
            app.state.recommendations = container.recommendations
            app.state.order_events = container.order_events
            app.state.orders = container.orders
            app.state.outbox = container.outbox
            app.state.oauth_states = container.oauth_states
            app.state.webhooks = container.webhooks
            app.state.line_oauth = container.line_oauth
            app.state.line_bot = container.line_bot
            app.state.customer_agent = container.customer_agent
            app.state.line_chat = container.line_chat
            yield
        finally:
            await broker.shutdown()
            await container.close()

    application = FastAPI(
        title="Food Ordering API",
        version="1.0.0",
        docs_url="/docs" if resolved_settings.app_env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.metrics = application_metrics
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_strings,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    application.add_middleware(RequestIDMiddleware, metrics=application_metrics)

    @application.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        status_code = 400
        if isinstance(exc, NotFoundError):
            status_code = 404
        elif isinstance(exc, ForbiddenError):
            status_code = 403
        elif isinstance(exc, ConflictError):
            status_code = 409
        elif isinstance(exc, InvalidInputError):
            status_code = 400
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc), "requestId": request.state.request_id},
        )

    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid request", "requestId": request.state.request_id},
        )

    api_prefix = "/api/v1"
    application.include_router(health.router, prefix=api_prefix)
    application.include_router(auth.router, prefix=api_prefix)
    application.include_router(products.router, prefix=api_prefix)
    application.include_router(recommendations.router, prefix=api_prefix)
    application.include_router(orders.router, prefix=api_prefix)
    application.include_router(staff.router, prefix=api_prefix)
    application.include_router(admin.router, prefix=api_prefix)
    application.include_router(line.router, prefix=api_prefix)
    application.include_router(metrics.router)
    return application


app = create_app()
