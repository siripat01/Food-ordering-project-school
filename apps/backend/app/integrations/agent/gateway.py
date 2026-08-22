from __future__ import annotations

import json
import os
from typing import Any

from app.core.config import Settings


class BoundLiteLLMGateway:
    def __init__(
        self,
        gateway: LiteLLMGateway,
        tools: list[Any],
        *,
        allow_cache: bool,
    ) -> None:
        self.gateway = gateway
        self.tools = tools
        self.allow_cache = allow_cache

    async def ainvoke(self, messages: list[Any]) -> Any:
        return await self.gateway.ainvoke(
            messages,
            tools=self.tools,
            allow_cache=self.allow_cache,
        )


class LiteLLMGateway:
    """In-process cost/complexity routing with safe, opt-in response caching."""

    def __init__(self, settings: Settings, *, router: Any | None = None) -> None:
        self.settings = settings
        self.router = router or self._build_router()

    def _deployment_configuration(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, list[str]]]]:
        api_key = self.settings.llm_api_key
        if api_key is None:
            raise RuntimeError("LLM gateway requires LLM_API_KEY")
        api_base = (
            str(self.settings.llm_api_base).rstrip("/") if self.settings.llm_api_base else None
        )

        def deployment(model_name: str, actual_model: str) -> dict[str, Any]:
            params: dict[str, Any] = {
                "model": actual_model,
                "api_key": api_key.get_secret_value(),
            }
            if api_base:
                params["api_base"] = api_base
            return {"model_name": model_name, "litellm_params": params}

        if self.settings.llm_complexity_routing_enabled:
            simple_group = f"{self.settings.llm_model}-simple"
            complex_group = f"{self.settings.llm_model}-complex"
            models = [
                deployment(simple_group, self.settings.llm_primary_model),
                deployment(complex_group, self.settings.llm_complex_model),
            ]
        else:
            simple_group = self.settings.llm_model
            complex_group = self.settings.llm_model
            models = [deployment(simple_group, self.settings.llm_primary_model)]

        fallback_groups: list[str] = []
        for index, actual_model in enumerate(self.settings.llm_fallback_models):
            group = f"{self.settings.llm_model}-fallback-{index + 1}"
            fallback_groups.append(group)
            models.append(deployment(group, actual_model))

        fallbacks = (
            [{simple_group: fallback_groups}, {complex_group: fallback_groups}]
            if fallback_groups and simple_group != complex_group
            else ([{simple_group: fallback_groups}] if fallback_groups else [])
        )
        if self.settings.llm_complexity_routing_enabled:
            complexity_config: dict[str, Any] = {
                "tiers": {
                    "SIMPLE": simple_group,
                    "MEDIUM": simple_group,
                    "COMPLEX": complex_group,
                    "REASONING": complex_group,
                },
                "default_model": simple_group,
                "classifier_type": self.settings.llm_complexity_classifier,
                "classifier_fallback": "heuristic",
                "classifier_context_window_size": 0,
                "escalation_keywords": [],
                "return_raw_model_name": True,
            }
            if self.settings.llm_complexity_classifier == "llm":
                complexity_config["classifier_llm_config"] = {
                    "model": simple_group,
                    "timeout_ms": self.settings.llm_complexity_classifier_timeout_ms,
                }
            models.append(
                {
                    "model_name": self.settings.llm_model,
                    "litellm_params": {
                        "model": "auto_router/complexity_router",
                        "complexity_router_config": complexity_config,
                        "complexity_router_default_model": simple_group,
                    },
                }
            )
        return models, fallbacks

    def _routing_model(self, messages: list[Any]) -> str:
        if not self.settings.llm_complexity_routing_enabled:
            return self.settings.llm_model
        user_text = ""
        for message in reversed(messages):
            if getattr(message, "type", None) == "human":
                content = getattr(message, "content", "")
                if isinstance(content, str):
                    user_text = content.casefold()
                break
        if any(
            keyword.strip().casefold() in user_text
            for keyword in self.settings.llm_complexity_keywords
            if keyword.strip()
        ):
            return f"{self.settings.llm_model}-complex"
        return self.settings.llm_model

    def _build_router(self) -> Any:
        # LiteLLM otherwise loads a nearby .env during import. Settings owns env-file
        # loading so third-party imports cannot mutate process configuration.
        os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
        import litellm
        from litellm.router import Router

        litellm.suppress_debug_info = True
        litellm.turn_off_message_logging = True
        model_list, fallbacks = self._deployment_configuration()
        return Router(
            model_list=model_list,
            fallbacks=fallbacks,
            routing_strategy=self.settings.llm_routing_strategy,
            num_retries=self.settings.llm_max_retries,
            timeout=self.settings.llm_timeout_seconds,
            cache_responses=self.settings.llm_cache_enabled,
            cache_kwargs={
                "type": "local",
                "ttl": self.settings.llm_cache_ttl_seconds,
                "max_size_in_memory": self.settings.llm_cache_max_entries,
                "max_size_per_item": 64_000,
            },
            set_verbose=False,
        )

    def bind_tools(self, tools: list[Any], *, allow_cache: bool = False) -> BoundLiteLLMGateway:
        return BoundLiteLLMGateway(self, tools, allow_cache=allow_cache)

    async def ainvoke(
        self,
        messages: list[Any],
        *,
        tools: list[Any],
        allow_cache: bool,
    ) -> Any:
        from langchain_core.messages import AIMessage, convert_to_openai_messages
        from langchain_core.utils.function_calling import convert_to_openai_tool

        openai_messages = convert_to_openai_messages(messages)
        tool_schemas = [convert_to_openai_tool(tool) for tool in tools]
        cache_control = (
            {"ttl": self.settings.llm_cache_ttl_seconds}
            if allow_cache and self.settings.llm_cache_enabled
            else {"no-store": True}
        )
        response = await self.router.acompletion(
            model=self._routing_model(messages),
            messages=openai_messages,
            tools=tool_schemas or None,
            tool_choice="auto" if tool_schemas else None,
            max_tokens=self.settings.llm_max_output_tokens,
            timeout=self.settings.llm_timeout_seconds,
            cache=cache_control,
        )
        choice = response.choices[0].message
        tool_calls = []
        for call in choice.tool_calls or []:
            arguments = call.function.arguments or "{}"
            try:
                parsed_arguments = json.loads(arguments)
            except (TypeError, json.JSONDecodeError):
                parsed_arguments = {}
            tool_calls.append(
                {
                    "name": call.function.name,
                    "args": parsed_arguments,
                    "id": call.id,
                    "type": "tool_call",
                }
            )
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return AIMessage(
            content=choice.content or "",
            tool_calls=tool_calls,
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            response_metadata={"model": getattr(response, "model", None)},
        )

    async def close(self) -> None:
        reset = getattr(self.router, "reset", None)
        if callable(reset):
            reset()
