from __future__ import annotations

import logging
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
from app.services.webhooks import WebhookEventService

logger = logging.getLogger(__name__)


class ServiceContainer:
    """Builds the application's services once, for any runtime that needs them.

    The API process, the Taskiq worker, and the outbox dispatcher all run the
    same service objects against the same MongoDB and Redis, so background work
    can never drift from the request path.
    """

    users: UserService
    auth_sessions: AuthSessionService
    products: ProductService
    recommendation_runtime: RecommendationModelRuntime
    recommendations: RecommendationService
    order_events: OrderEventBroker
    order_updates: OrderUpdateDispatcher
    outbox: OutboxService
    idempotency: IdempotencyService
    orders: OrderService
    notifier: LineOrderStatusNotifier
    order_workflow: OrderWorkflowService
    oauth_states: OAuthStateService
    webhooks: WebhookEventService
    line_oauth: LineOAuthClient
    customer_agent: CustomerAgentService
    line_chat: LineChatService

    def __init__(
        self,
        settings: Settings,
        *,
        metrics: ApplicationMetrics | None = None,
    ) -> None:
        self.settings = settings
        self.metrics = metrics or ApplicationMetrics()
        self.db = MongoDatabase(settings)
        self.redis = RedisDatabase(settings)
        self.line_bot = LineBotClient(settings)
        self.http_client: httpx.AsyncClient | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        settings = self.settings
        await self.db.connect()
        await self.redis.connect()
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds)
        )
        await self.line_bot.start()

        self.users = UserService(self.db)
        self.auth_sessions = AuthSessionService(self.redis.client, settings)
        self.products = ProductService(self.db)
        self.recommendation_runtime = RecommendationModelRuntime(
            db=self.db,
            settings=settings,
            metrics=self.metrics,
            redis=self.redis.client,
        )
        self.recommendations = RecommendationService(
            db=self.db,
            products=self.products,
            settings=settings,
            http_client=self.http_client,
            metrics=self.metrics,
            runtime=self.recommendation_runtime,
        )
        self.order_events = OrderEventBroker(queue_size=settings.sse_subscriber_queue_size)
        self.order_updates = OrderUpdateDispatcher(
            broker=self.order_events,
            recommendations=self.recommendations,
        )
        self.outbox = OutboxService(OutboxRepository(self.db))
        self.idempotency = IdempotencyService(self.db)
        self.orders = OrderService(
            self.db,
            metrics=self.metrics,
            updates=self.order_updates,
            outbox=self.outbox,
        )
        self.notifier = LineOrderStatusNotifier(settings=settings, users=self.users)
        self.order_workflow = OrderWorkflowService(
            orders=self.orders,
            notifier=self.notifier,
            push_messages=self._enqueue_push,
        )
        self.oauth_states = OAuthStateService(self.db, settings)
        self.webhooks = WebhookEventService(self.db)
        self.line_oauth = LineOAuthClient(settings, self.http_client)
        self.customer_agent = CustomerAgentService(
            settings,
            CustomerToolFactory(self.products, self.orders),
            metrics=self.metrics,
            redis=self.redis.client,
        )
        self.line_chat = LineChatService(
            settings=settings,
            users=self.users,
            line_bot=self.line_bot,
            agent=self.customer_agent,
            push_messages=self._enqueue_push,
            reply_messages=self._enqueue_reply,
        )
        self._started = True
        logger.info("service_container_started")

    @staticmethod
    async def _enqueue_push(
        line_user_id: str,
        messages: list[dict[str, Any]],
        correlation_id: str | None,
    ) -> None:
        """Hand LINE delivery to the queue instead of calling LINE inline."""
        from app.jobs.line import push_line

        await push_line.kiq(
            line_user_id=line_user_id,
            messages=messages,
            correlation_id=correlation_id,
        )

    @staticmethod
    async def _enqueue_reply(
        reply_token: str,
        messages: list[dict[str, Any]],
        correlation_id: str | None,
    ) -> None:
        """Hand a webhook reply to the queue; reply tokens are single-use."""
        from app.jobs.line import reply_line

        await reply_line.kiq(
            reply_token=reply_token,
            messages=messages,
            correlation_id=correlation_id,
        )

    async def close(self) -> None:
        if not self._started:
            return
        await self.customer_agent.close()
        await self.order_updates.close()
        await self.line_bot.close()
        if self.http_client is not None:
            await self.http_client.aclose()
        await self.db.close()
        await self.redis.close()
        self._started = False
