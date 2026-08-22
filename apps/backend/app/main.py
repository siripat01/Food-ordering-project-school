from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
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
from app.core.middleware import RequestIDMiddleware
from app.core.observability import ApplicationMetrics, configure_logging
from app.db.mongodb import MongoDatabase
from app.domain.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.integrations.agent.service import CustomerAgentService
from app.integrations.agent.tools import CustomerToolFactory
from app.integrations.line import LineBotClient, LineOAuthClient
from app.services.oauth import OAuthStateService
from app.services.order_updates import (
    LineOrderStatusNotifier,
    OrderEventBroker,
    OrderUpdateDispatcher,
)
from app.services.orders import OrderService
from app.services.products import ProductService
from app.services.recommendation_runtime import RecommendationModelRuntime
from app.services.recommendations import RecommendationService
from app.services.users import UserService
from app.services.webhooks import WebhookEventService


def create_app(settings: Settings | None = None, *, initialize_clients: bool = True) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(level=resolved_settings.log_level, json_logs=resolved_settings.log_json)
    application_metrics = ApplicationMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not initialize_clients:
            yield
            return
        db = MongoDatabase(resolved_settings)
        http_client: httpx.AsyncClient | None = None
        line_bot = LineBotClient(resolved_settings)
        customer_agent: CustomerAgentService | None = None
        order_updates: OrderUpdateDispatcher | None = None
        try:
            await db.connect()
            http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(resolved_settings.llm_timeout_seconds)
            )
            await line_bot.start()

            app.state.db = db
            app.state.http_client = http_client
            app.state.users = UserService(db)
            app.state.products = ProductService(db)
            app.state.recommendation_runtime = RecommendationModelRuntime(
                db=db,
                settings=resolved_settings,
                metrics=application_metrics,
            )
            app.state.recommendations = RecommendationService(
                db=db,
                products=app.state.products,
                settings=resolved_settings,
                http_client=http_client,
                metrics=application_metrics,
                runtime=app.state.recommendation_runtime,
            )
            app.state.order_events = OrderEventBroker(
                queue_size=resolved_settings.sse_subscriber_queue_size
            )
            order_updates = OrderUpdateDispatcher(
                broker=app.state.order_events,
                notifier=LineOrderStatusNotifier(
                    settings=resolved_settings,
                    users=app.state.users,
                    line_bot=line_bot,
                ),
                recommendations=app.state.recommendations,
            )
            app.state.orders = OrderService(
                db,
                metrics=application_metrics,
                updates=order_updates,
            )
            app.state.oauth_states = OAuthStateService(db, resolved_settings)
            app.state.webhooks = WebhookEventService(db)
            app.state.line_oauth = LineOAuthClient(resolved_settings, http_client)
            app.state.line_bot = line_bot
            customer_agent = CustomerAgentService(
                resolved_settings,
                CustomerToolFactory(app.state.products, app.state.orders),
                metrics=application_metrics,
            )
            app.state.customer_agent = customer_agent
            yield
        finally:
            if customer_agent:
                await customer_agent.close()
            if order_updates:
                await order_updates.close()
            await line_bot.close()
            if http_client:
                await http_client.aclose()
            await db.close()

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
