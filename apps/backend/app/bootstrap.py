from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.observability import ApplicationMetrics
from app.db.mongodb import MongoDatabase
from app.db.redis import RedisDatabase
from app.integrations.agent.service import CustomerAgentService
from app.integrations.agent.tools import CustomerToolFactory
from app.integrations.line import LineBotClient, LineOAuthClient
from app.services.auth_sessions import AuthSessionService
from app.services.idempotency import IdempotencyService
from app.services.line_chat import LineChatService
from app.services.oauth import OAuthStateService
from app.services.order_updates import (
    LineOrderStatusNotifier,
    OrderEventBroker,
    OrderUpdateDispatcher,
)
from app.services.order_workflow import OrderWorkflowService
from app.services.orders import OrderService
from app.services.outbox import OutboxRepository, OutboxService
from app.services.products import ProductService
from app.services.recommendation_runtime import RecommendationModelRuntime
from app.services.recommendations import RecommendationService
from app.services.users import UserService

PushMessages = Callable[[str, list[dict[str, Any]], str | None], Awaitable[None]]
ReplyMessages = Callable[[str, list[dict[str, Any]], str | None], Awaitable[None]]


@dataclass(slots=True)
class ApiServices:
    """Dependencies owned by one FastAPI process.

    This is deliberately a data object, not a service locator with lifecycle
    or business behavior. Construction and cleanup live in the composition-root
    functions below.
    """

    db: MongoDatabase
    redis: RedisDatabase
    http_client: httpx.AsyncClient
    line_bot: LineBotClient
    users: UserService
    auth_sessions: AuthSessionService
    products: ProductService
    recommendation_runtime: RecommendationModelRuntime
    recommendations: RecommendationService
    order_events: OrderEventBroker
    order_updates: OrderUpdateDispatcher
    outbox: OutboxService
    orders: OrderService
    oauth_states: OAuthStateService
    line_oauth: LineOAuthClient


@dataclass(slots=True)
class WorkerServices:
    """Minimal dependency graph required by Taskiq task handlers."""

    db: MongoDatabase
    redis: RedisDatabase
    line_bot: LineBotClient
    users: UserService
    products: ProductService
    outbox: OutboxService
    idempotency: IdempotencyService
    orders: OrderService
    notifier: LineOrderStatusNotifier
    order_workflow: OrderWorkflowService
    customer_agent: CustomerAgentService
    line_chat: LineChatService


async def enqueue_line_push(
    line_user_id: str,
    messages: list[dict[str, Any]],
    correlation_id: str | None,
) -> None:
    """Queue LINE push delivery without coupling services to Taskiq imports."""

    from app.jobs.line import push_line

    await push_line.kiq(
        line_user_id=line_user_id,
        messages=messages,
        correlation_id=correlation_id,
    )


async def enqueue_line_reply(
    reply_token: str,
    messages: list[dict[str, Any]],
    correlation_id: str | None,
) -> None:
    """Queue LINE reply delivery without coupling services to Taskiq imports."""

    from app.jobs.line import reply_line

    await reply_line.kiq(
        reply_token=reply_token,
        messages=messages,
        correlation_id=correlation_id,
    )


async def _close_quietly(awaitable: Awaitable[Any]) -> None:
    """Best-effort cleanup used only while unwinding a failed startup."""

    with contextlib.suppress(Exception):
        await awaitable


async def build_api_services(
    settings: Settings,
    *,
    metrics: ApplicationMetrics | None = None,
) -> ApiServices:
    """Build exactly the dependency graph used by the FastAPI process."""

    resolved_metrics = metrics or ApplicationMetrics()
    db = MongoDatabase(settings)
    redis = RedisDatabase(settings)
    line_bot = LineBotClient(settings)
    http_client: httpx.AsyncClient | None = None
    order_updates: OrderUpdateDispatcher | None = None

    try:
        await db.connect()
        await redis.connect()
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds))
        await line_bot.start()

        users = UserService(db)
        auth_sessions = AuthSessionService(redis.client, settings)
        products = ProductService(db)
        recommendation_runtime = RecommendationModelRuntime(
            db=db,
            settings=settings,
            metrics=resolved_metrics,
            redis=redis.client,
        )
        recommendations = RecommendationService(
            db=db,
            products=products,
            settings=settings,
            http_client=http_client,
            metrics=resolved_metrics,
            runtime=recommendation_runtime,
        )
        order_events = OrderEventBroker(queue_size=settings.sse_subscriber_queue_size)
        order_updates = OrderUpdateDispatcher(
            broker=order_events,
            recommendations=recommendations,
        )
        outbox = OutboxService(OutboxRepository(db))
        orders = OrderService(
            db,
            metrics=resolved_metrics,
            updates=order_updates,
            outbox=outbox,
        )
        oauth_states = OAuthStateService(db, settings)
        line_oauth = LineOAuthClient(settings, http_client)

        return ApiServices(
            db=db,
            redis=redis,
            http_client=http_client,
            line_bot=line_bot,
            users=users,
            auth_sessions=auth_sessions,
            products=products,
            recommendation_runtime=recommendation_runtime,
            recommendations=recommendations,
            order_events=order_events,
            order_updates=order_updates,
            outbox=outbox,
            orders=orders,
            oauth_states=oauth_states,
            line_oauth=line_oauth,
        )
    except Exception:
        if order_updates is not None:
            await _close_quietly(order_updates.close())
        await _close_quietly(line_bot.close())
        if http_client is not None:
            await _close_quietly(http_client.aclose())
        await _close_quietly(redis.close())
        await _close_quietly(db.close())
        raise


async def close_api_services(services: ApiServices) -> None:
    """Close resources owned by one FastAPI process."""

    await services.order_updates.close()
    await services.line_bot.close()
    await services.http_client.aclose()
    await services.redis.close()
    await services.db.close()


async def build_worker_services(
    settings: Settings,
    *,
    metrics: ApplicationMetrics | None = None,
    push_messages: PushMessages = enqueue_line_push,
    reply_messages: ReplyMessages = enqueue_line_reply,
) -> WorkerServices:
    """Build only the dependencies needed by Taskiq workers."""

    resolved_metrics = metrics or ApplicationMetrics()
    db = MongoDatabase(settings)
    redis = RedisDatabase(settings)
    line_bot = LineBotClient(settings)
    customer_agent: CustomerAgentService | None = None

    try:
        await db.connect()
        await redis.connect()
        await line_bot.start()

        users = UserService(db)
        products = ProductService(db)
        outbox = OutboxService(OutboxRepository(db))
        idempotency = IdempotencyService(db)
        # Worker-side writes still need the outbox, but SSE/recommendation fan-out
        # belongs to the API process and is intentionally not constructed here.
        orders = OrderService(
            db,
            metrics=resolved_metrics,
            outbox=outbox,
        )
        notifier = LineOrderStatusNotifier(settings=settings, users=users)
        order_workflow = OrderWorkflowService(
            orders=orders,
            notifier=notifier,
            push_messages=push_messages,
        )
        customer_agent = CustomerAgentService(
            settings,
            CustomerToolFactory(products, orders),
            metrics=resolved_metrics,
            redis=redis.client,
        )
        line_chat = LineChatService(
            settings=settings,
            users=users,
            line_bot=line_bot,
            agent=customer_agent,
            push_messages=push_messages,
            reply_messages=reply_messages,
        )

        return WorkerServices(
            db=db,
            redis=redis,
            line_bot=line_bot,
            users=users,
            products=products,
            outbox=outbox,
            idempotency=idempotency,
            orders=orders,
            notifier=notifier,
            order_workflow=order_workflow,
            customer_agent=customer_agent,
            line_chat=line_chat,
        )
    except Exception:
        if customer_agent is not None:
            await _close_quietly(customer_agent.close())
        await _close_quietly(line_bot.close())
        await _close_quietly(redis.close())
        await _close_quietly(db.close())
        raise


async def close_worker_services(services: WorkerServices) -> None:
    """Close resources owned by one Taskiq worker process."""

    await services.customer_agent.close()
    await services.line_bot.close()
    await services.redis.close()
    await services.db.close()
