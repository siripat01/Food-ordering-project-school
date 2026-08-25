from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.jobs.agent import IDEMPOTENCY_SCOPE, process_agent_message
from app.jobs.line import push_line, reply_line
from app.jobs.order import cancel_order, process_order, update_order_status


def context(services) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(services=services),
        message=SimpleNamespace(task_id="task-1"),
    )


@pytest.mark.asyncio
async def test_order_process_task_delegates_to_the_order_workflow() -> None:
    services = SimpleNamespace(order_workflow=AsyncMock())

    await process_order.original_func(
        "order-1", correlation_id="request-1", context=context(services)
    )

    services.order_workflow.process_created.assert_awaited_once_with(
        "order-1", correlation_id="request-1"
    )


@pytest.mark.asyncio
async def test_order_update_status_task_delegates_to_the_order_workflow() -> None:
    services = SimpleNamespace(order_workflow=AsyncMock())

    await update_order_status.original_func(
        "order-1", correlation_id="request-1", context=context(services)
    )

    services.order_workflow.process_status_change.assert_awaited_once_with(
        "order-1", correlation_id="request-1"
    )


@pytest.mark.asyncio
async def test_order_cancel_task_delegates_to_the_order_workflow() -> None:
    services = SimpleNamespace(order_workflow=AsyncMock())

    await cancel_order.original_func(
        "order-1", "user-1", correlation_id="request-1", context=context(services)
    )

    services.order_workflow.cancel.assert_awaited_once_with(
        "order-1", user_id="user-1", correlation_id="request-1"
    )


@pytest.mark.asyncio
async def test_order_workflow_notifies_through_the_line_push_task() -> None:
    from app.domain.orders import OrderStatus
    from app.services.order_workflow import OrderWorkflowService

    order = SimpleNamespace(id="order-1", user_id="user-1", status=OrderStatus.CONFIRMED)
    orders = AsyncMock()
    orders.get.return_value = order
    notifier = AsyncMock()
    notifier.resolve_recipient.return_value = "line-recipient"
    notifier.build_status_messages = lambda _order: [{"type": "text", "text": "ok"}]
    push = AsyncMock()

    service = OrderWorkflowService(orders=orders, notifier=notifier, push_messages=push)
    await service.process_status_change("order-1", correlation_id="request-1")

    orders.get.assert_awaited_once_with("order-1")
    push.assert_awaited_once_with(
        "line-recipient", [{"type": "text", "text": "ok"}], "request-1"
    )


@pytest.mark.asyncio
async def test_order_workflow_cancel_delegates_to_the_order_service() -> None:
    from app.domain.orders import OrderStatus
    from app.services.order_workflow import OrderWorkflowService

    orders = AsyncMock()
    orders.cancel_own.return_value = SimpleNamespace(id="order-1", status=OrderStatus.CANCELLED)

    service = OrderWorkflowService(
        orders=orders, notifier=AsyncMock(), push_messages=AsyncMock()
    )
    await service.cancel("order-1", user_id="user-1", correlation_id="request-1")

    orders.cancel_own.assert_awaited_once_with(order_id="order-1", user_id="user-1")


@pytest.mark.asyncio
async def test_line_push_task_delegates_to_the_line_client() -> None:
    services = SimpleNamespace(line_bot=AsyncMock())
    messages = [{"type": "text", "text": "ready"}]

    await push_line.original_func(
        "line-recipient", messages, correlation_id="request-1", context=context(services)
    )

    services.line_bot.push_messages.assert_awaited_once_with(
        line_user_id="line-recipient", messages=messages
    )


@pytest.mark.asyncio
async def test_line_reply_task_delegates_to_the_line_client() -> None:
    services = SimpleNamespace(line_bot=AsyncMock())
    messages = [{"type": "text", "text": "hello"}]

    await reply_line.original_func(
        "reply-token", messages, correlation_id="request-1", context=context(services)
    )

    services.line_bot.reply_messages.assert_awaited_once_with(
        reply_token="reply-token", messages=messages
    )


@pytest.mark.asyncio
async def test_line_push_task_logs_no_recipient_or_message_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = SimpleNamespace(line_bot=AsyncMock())

    with caplog.at_level("INFO"):
        await push_line.original_func(
            "line-recipient",
            [{"type": "text", "text": "private order details"}],
            correlation_id="request-1",
            context=context(services),
        )

    assert "line_push_task_started" in caplog.text
    assert "line-recipient" not in caplog.text
    assert "private order details" not in caplog.text


@pytest.mark.asyncio
async def test_agent_task_delegates_to_the_chat_service() -> None:
    services = SimpleNamespace(line_chat=AsyncMock(), idempotency=AsyncMock())
    services.idempotency.claim.return_value = True

    await process_agent_message.original_func(
        "line-user",
        "ขอผัดกะเพรา",
        reply_token="reply-token",
        idempotency_key="line:event-1",
        correlation_id="request-1",
        context=context(services),
    )

    services.idempotency.claim.assert_awaited_once_with(
        scope=IDEMPOTENCY_SCOPE, key="line:event-1"
    )
    services.line_chat.handle_text_message.assert_awaited_once_with(
        line_user_id="line-user",
        text="ขอผัดกะเพรา",
        reply_token="reply-token",
        idempotency_key="line:event-1",
        correlation_id="request-1",
        show_loading=True,
    )


@pytest.mark.asyncio
async def test_duplicate_agent_delivery_is_skipped() -> None:
    services = SimpleNamespace(line_chat=AsyncMock(), idempotency=AsyncMock())
    services.idempotency.claim.return_value = False

    await process_agent_message.original_func(
        "line-user",
        "ขอผัดกะเพรา",
        idempotency_key="line:event-1",
        context=context(services),
    )

    services.line_chat.handle_text_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_agent_run_releases_its_idempotency_claim_for_retry() -> None:
    services = SimpleNamespace(line_chat=AsyncMock(), idempotency=AsyncMock())
    services.idempotency.claim.return_value = True
    services.line_chat.handle_text_message.side_effect = RuntimeError("llm down")

    with pytest.raises(RuntimeError):
        await process_agent_message.original_func(
            "line-user",
            "ขอผัดกะเพรา",
            idempotency_key="line:event-1",
            context=context(services),
        )

    services.idempotency.release.assert_awaited_once_with(
        scope=IDEMPOTENCY_SCOPE, key="line:event-1"
    )
