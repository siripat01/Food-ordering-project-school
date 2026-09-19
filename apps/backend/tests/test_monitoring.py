from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.core.observability import ApplicationMetrics, MetricsHTTPServer
from app.jobs.context import track_task


def test_application_metrics_record_background_task_and_outbox_outcomes() -> None:
    metrics = ApplicationMetrics()

    metrics.record_task("order.process", "success", 0.25)
    metrics.record_task("order.process", "failed", 0.5)
    metrics.record_outbox("order.created", "sent")
    metrics.record_outbox("order.created", "failed")
    metrics.set_outbox_oldest_age(12.5)

    rendered = metrics.render().decode()

    assert (
        'food_ordering_task_executions_total{outcome="success",task="order.process"}'
        in rendered
    )
    assert (
        'food_ordering_task_executions_total{outcome="failed",task="order.process"}' in rendered
    )
    assert (
        'food_ordering_outbox_events_total{event_type="order.created",outcome="sent"}'
        in rendered
    )
    assert (
        'food_ordering_outbox_events_total{event_type="order.created",outcome="failed"}'
        in rendered
    )
    assert "food_ordering_outbox_oldest_pending_age_seconds 12.5" in rendered


@pytest.mark.asyncio
async def test_track_task_records_success_and_failure_without_leaking_ids() -> None:
    metrics = ApplicationMetrics()
    services = SimpleNamespace(metrics=metrics)
    context = SimpleNamespace(
        state=SimpleNamespace(services=services),
        message=SimpleNamespace(task_id="task-private"),
    )

    @track_task("test.task")
    async def successful(*, context):
        return "ok"

    @track_task("test.task")
    async def failed(*, context):
        raise RuntimeError("private failure")

    assert await successful(context=context) == "ok"
    with pytest.raises(RuntimeError, match="private failure"):
        await failed(context=context)

    rendered = metrics.render().decode()
    assert 'task="test.task"' in rendered
    assert 'outcome="success"' in rendered
    assert 'outcome="failed"' in rendered
    assert "task-private" not in rendered
    assert "private failure" not in rendered


def test_settings_define_private_background_metrics_ports() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="test-secret-0123456789abcdef0123456789",
        cookie_secure=False,
    )

    assert settings.worker_metrics_port == 9101
    assert settings.dispatcher_metrics_port == 9102


@pytest.mark.asyncio
async def test_metrics_http_server_uses_custom_registry(monkeypatch) -> None:
    metrics = ApplicationMetrics()
    metrics.record_task("test.task", "success", 0.1)
    captured = {}

    class FakeServer:
        def shutdown(self):
            captured["shutdown"] = True

        def server_close(self):
            captured["closed"] = True

    def start_http_server(port, *, addr, registry):
        captured.update(port=port, addr=addr, registry=registry)
        return FakeServer(), None

    monkeypatch.setattr("app.core.observability.start_http_server", start_http_server)

    server = MetricsHTTPServer(metrics.registry)
    server.start(port=9101, host="127.0.0.1")
    server.stop()

    assert captured["port"] == 9101
    assert captured["addr"] == "127.0.0.1"
    assert captured["registry"] is metrics.registry
    assert captured["shutdown"] is True
    assert captured["closed"] is True
