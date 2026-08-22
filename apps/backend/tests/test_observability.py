from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.core.observability import ApplicationMetrics, JsonLogFormatter
from app.integrations.agent.service import CustomerAgentService
from app.main import create_app


def test_json_logging_redacts_credentials_and_pii() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "Bearer fake.header.signature "
            "mongodb+srv://fake-user:fake-password@example.invalid/database "
            "customer@example.invalid"
        ),
        args=(),
        exc_info=None,
    )
    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["event"].count("[REDACTED") == 3
    assert "fake-password" not in payload["event"]
    assert "customer@example.invalid" not in payload["event"]


@pytest.mark.asyncio
async def test_request_metrics_and_request_id_are_exposed(settings) -> None:
    app = create_app(settings, initialize_clients=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/health/live", headers={"X-Request-ID": "phase2-test"}
        )
        metrics = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "phase2-test"
    assert metrics.status_code == 200
    assert "food_ordering_http_requests_total" in metrics.text
    assert 'route="/api/v1/health/live"' in metrics.text


class FakeModel:
    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages):
        return SimpleNamespace(
            content="done",
            tool_calls=[],
            usage_metadata={"input_tokens": 12, "output_tokens": 3},
        )


@pytest.mark.asyncio
async def test_llm_token_and_cost_metrics_are_recorded(settings) -> None:
    settings.llm_input_cost_per_million = 1
    settings.llm_output_cost_per_million = 2
    metrics = ApplicationMetrics()
    service = CustomerAgentService(
        settings,
        SimpleNamespace(build=lambda **_kwargs: []),
        metrics=metrics,
    )
    service.model = FakeModel()
    identity = SimpleNamespace(id="customer-id")

    result = await service.chat(
        identity=identity,
        message="hello",
        idempotency_key="request-12345678",
    )
    rendered = metrics.render().decode()

    assert result == "done"
    model = settings.llm_model
    assert (
        f'food_ordering_llm_tokens_total{{direction="input",model="{model}"}} 12.0' in rendered
    )
    assert (
        f'food_ordering_llm_tokens_total{{direction="output",model="{model}"}} 3.0' in rendered
    )
    assert "food_ordering_llm_estimated_cost_usd_total" in rendered
