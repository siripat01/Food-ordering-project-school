from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from app.core.config import Settings
from app.integrations.agent.gateway import LiteLLMGateway
from app.integrations.agent.service import visible_model_text


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="gateway response", tool_calls=[])
        usage = SimpleNamespace(prompt_tokens=7, completion_tokens=2)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
            model="deepseek-v4-flash",
        )


def gateway_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="gateway-test-secret-0123456789abcdef",
        cookie_secure=False,
        llm_enabled=True,
        llm_api_key="fake-provider-key",
        llm_api_base="https://api.deepseek.com",
        llm_model="ordering-assistant",
        llm_primary_model="deepseek/deepseek-v4-flash",
        llm_complex_model="deepseek/deepseek-v4-pro",
        llm_fallback_models=["openai/gpt-4.1-mini"],
        llm_cache_enabled=True,
        llm_cache_ttl_seconds=90,
    )


def test_provider_reasoning_blocks_are_not_returned_to_customers() -> None:
    assert (
        visible_model_text("<think>private reasoning</think>\nคำตอบสำหรับลูกค้า")
        == "คำตอบสำหรับลูกค้า"
    )
    assert visible_model_text("<think>unfinished private reasoning") == ""


def test_gateway_accepts_minimax_openai_compatible_configuration() -> None:
    settings = gateway_settings()
    settings.llm_api_base = "https://api.minimax.io/v1"
    settings.llm_primary_model = "minimax/MiniMax-M2.7"
    settings.llm_complex_model = "minimax/MiniMax-M2.7"
    settings.llm_complexity_routing_enabled = False
    gateway = LiteLLMGateway(settings, router=FakeRouter())

    deployments, fallbacks = gateway._deployment_configuration()

    assert fallbacks == [{"ordering-assistant": ["ordering-assistant-fallback-1"]}]
    assert deployments[0]["litellm_params"] == {
        "model": "minimax/MiniMax-M2.7",
        "api_key": "fake-provider-key",
        "api_base": "https://api.minimax.io/v1",
    }


def test_gateway_builds_cost_tiers_and_fallback_model_groups() -> None:
    gateway = LiteLLMGateway(gateway_settings(), router=FakeRouter())

    deployments, fallbacks = gateway._deployment_configuration()

    assert [item["model_name"] for item in deployments] == [
        "ordering-assistant-simple",
        "ordering-assistant-complex",
        "ordering-assistant-fallback-1",
        "ordering-assistant",
    ]
    assert deployments[0]["litellm_params"]["model"] == ("deepseek/deepseek-v4-flash")
    assert deployments[1]["litellm_params"]["model"] == ("deepseek/deepseek-v4-pro")
    router_params = deployments[-1]["litellm_params"]
    assert router_params["model"] == "auto_router/complexity_router"
    assert router_params["complexity_router_config"]["tiers"] == {
        "SIMPLE": "ordering-assistant-simple",
        "MEDIUM": "ordering-assistant-simple",
        "COMPLEX": "ordering-assistant-complex",
        "REASONING": "ordering-assistant-complex",
    }
    assert router_params["complexity_router_config"]["escalation_keywords"] == []
    assert fallbacks == [
        {"ordering-assistant-simple": ["ordering-assistant-fallback-1"]},
        {"ordering-assistant-complex": ["ordering-assistant-fallback-1"]},
    ]


def test_gateway_can_use_cheap_llm_as_classifier() -> None:
    settings = gateway_settings()
    settings.llm_complexity_classifier = "llm"
    gateway = LiteLLMGateway(settings, router=FakeRouter())

    deployments, _ = gateway._deployment_configuration()
    config = deployments[-1]["litellm_params"]["complexity_router_config"]

    assert config["classifier_type"] == "llm"
    assert config["classifier_llm_config"] == {
        "model": "ordering-assistant-simple",
        "timeout_ms": 1500,
    }
    assert config["classifier_fallback"] == "heuristic"
    assert config["classifier_context_window_size"] == 0


@pytest.mark.asyncio
async def test_agent_gateway_disables_cache_for_tool_capable_requests() -> None:
    router = FakeRouter()
    gateway = LiteLLMGateway(gateway_settings(), router=router)

    response = await gateway.bind_tools([]).ainvoke([HumanMessage(content="hello")])

    assert response.content == "gateway response"
    assert response.usage_metadata == {
        "input_tokens": 7,
        "output_tokens": 2,
        "total_tokens": 9,
    }
    assert router.calls[0]["model"] == "ordering-assistant"
    assert router.calls[0]["cache"] == {"no-store": True}


@pytest.mark.asyncio
async def test_public_read_only_gateway_cache_is_opt_in() -> None:
    router = FakeRouter()
    gateway = LiteLLMGateway(gateway_settings(), router=router)

    await gateway.bind_tools([], allow_cache=True).ainvoke(
        [HumanMessage(content="public menu summary")]
    )

    assert router.calls[0]["cache"] == {"ttl": 90}


@pytest.mark.asyncio
async def test_thai_domain_complexity_override_routes_to_capable_model() -> None:
    router = FakeRouter()
    gateway = LiteLLMGateway(gateway_settings(), router=router)

    await gateway.bind_tools([]).ainvoke(
        [HumanMessage(content="ช่วยจัดหลายเมนูตามงบประมาณและข้อจำกัดเรื่องแพ้อาหาร")]
    )

    assert router.calls[0]["model"] == "ordering-assistant-complex"


@pytest.mark.asyncio
async def test_installed_litellm_router_can_be_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITELLM_MODE", "PRODUCTION")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    settings = gateway_settings()
    settings.llm_cache_enabled = False
    gateway = LiteLLMGateway(settings)

    assert gateway.router is not None
    strategy = gateway.router.complexity_routers["ordering-assistant"][0].strategy

    simple = await strategy.async_pre_routing_hook(
        model="ordering-assistant",
        request_kwargs={},
        messages=[{"role": "user", "content": "ขอดูเมนู"}],
    )
    complex_request = await strategy.async_pre_routing_hook(
        model="ordering-assistant",
        request_kwargs={},
        messages=[
            {
                "role": "user",
                "content": (
                    "Analyze this step by step, compare and contrast the options, "
                    "then explain the reasoning."
                ),
            }
        ],
    )

    assert simple is not None
    assert simple.model == "ordering-assistant-simple"
    assert complex_request is not None
    assert complex_request.model == "ordering-assistant-complex"

    await gateway.close()
