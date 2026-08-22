from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

from app.core.config import Settings
from app.core.observability import ApplicationMetrics
from app.domain.users import CurrentUser
from app.integrations.agent.gateway import LiteLLMGateway
from app.integrations.agent.tools import CustomerToolFactory, ScopedTool

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryEntry:
    messages: deque[tuple[str, str]]
    touched_at: datetime


class BoundedMemoryStore:
    def __init__(self, *, max_messages: int, ttl_minutes: int) -> None:
        self.max_messages = max_messages
        self.ttl = timedelta(minutes=ttl_minutes)
        self.entries: dict[str, MemoryEntry] = {}

    def get(self, user_id: str) -> list[tuple[str, str]]:
        now = datetime.now(UTC)
        self.entries = {
            key: entry
            for key, entry in self.entries.items()
            if now - entry.touched_at <= self.ttl
        }
        entry = self.entries.get(user_id)
        if entry is None:
            entry = MemoryEntry(deque(maxlen=self.max_messages), now)
            self.entries[user_id] = entry
        entry.touched_at = now
        return list(entry.messages)

    def append(self, user_id: str, role: str, content: str) -> None:
        self.get(user_id)
        entry = self.entries[user_id]
        entry.messages.append((role, content[:4000]))
        entry.touched_at = datetime.now(UTC)


class CustomerAgentService:
    def __init__(
        self,
        settings: Settings,
        tools: CustomerToolFactory,
        *,
        metrics: ApplicationMetrics | None = None,
    ) -> None:
        self.settings = settings
        self.tool_factory = tools
        self.metrics = metrics
        self.memory = BoundedMemoryStore(
            max_messages=settings.llm_memory_messages,
            ttl_minutes=settings.llm_memory_ttl_minutes,
        )
        self.model: Any = None
        if settings.llm_enabled:
            self.model = LiteLLMGateway(settings)

    @staticmethod
    def _to_langchain_tools(scoped: list[ScopedTool]) -> list[Any]:
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(
                coroutine=tool.coroutine,
                name=tool.name,
                description=tool.description,
            )
            for tool in scoped
        ]

    @staticmethod
    def _token_usage(message: Any) -> tuple[int, int]:
        usage = getattr(message, "usage_metadata", None) or {}
        if usage:
            return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        response_metadata = getattr(message, "response_metadata", None) or {}
        token_usage = response_metadata.get("token_usage", {})
        return int(token_usage.get("prompt_tokens", 0)), int(
            token_usage.get("completion_tokens", 0)
        )

    def _record_llm_metrics(
        self,
        *,
        duration_seconds: float,
        input_tokens: int,
        output_tokens: int,
        failed: bool,
    ) -> None:
        if not self.metrics:
            return
        estimated_cost = (
            input_tokens * self.settings.llm_input_cost_per_million
            + output_tokens * self.settings.llm_output_cost_per_million
        ) / 1_000_000
        self.metrics.observe_llm(
            model=self.settings.llm_model,
            duration_seconds=duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            failed=failed,
        )

    async def chat(
        self,
        *,
        identity: CurrentUser,
        message: str,
        idempotency_key: str,
    ) -> str:
        if self.model is None:
            return "ขออภัย ระบบผู้ช่วยสั่งอาหารยังไม่เปิดใช้งานในขณะนี้"
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        scoped = self.tool_factory.build(identity=identity, idempotency_key=idempotency_key)
        tools = self._to_langchain_tools(scoped)
        tool_by_name = {tool.name: tool for tool in tools}
        history = self.memory.get(identity.id)
        messages: list[Any] = [
            SystemMessage(
                content=(
                    "You are a Thai food-ordering assistant for an authenticated customer. "
                    "Use only the provided customer tools. Never request or reveal "
                    "chain-of-thought. Never infer identity or prices; trusted tools enforce "
                    "both. Keep responses concise "
                    "and do not repeat personal data."
                )
            )
        ]
        for role, content in history:
            history_message = (
                HumanMessage(content=content) if role == "user" else AIMessage(content=content)
            )
            messages.append(history_message)
        messages.append(HumanMessage(content=message[:4000]))
        model_with_tools = self.model.bind_tools(tools)
        final_text = "ไม่สามารถดำเนินการให้เสร็จได้ กรุณาลองใหม่"
        started_at = perf_counter()
        input_tokens = 0
        output_tokens = 0
        try:
            for _ in range(self.settings.llm_max_tool_iterations):
                ai_message = await model_with_tools.ainvoke(messages)
                used_input, used_output = self._token_usage(ai_message)
                input_tokens += used_input
                output_tokens += used_output
                messages.append(ai_message)
                if not ai_message.tool_calls:
                    content = ai_message.content
                    final_text = content if isinstance(content, str) else str(content)
                    break
                for call in ai_message.tool_calls:
                    tool = tool_by_name.get(call["name"])
                    if tool is None:
                        result = "Tool is not authorized for this agent"
                    else:
                        try:
                            result = await tool.ainvoke(call.get("args", {}))
                        except Exception:
                            result = "The requested operation could not be completed"
                    messages.append(
                        ToolMessage(content=str(result)[:8000], tool_call_id=call["id"])
                    )
        except Exception as exc:
            duration_seconds = perf_counter() - started_at
            self._record_llm_metrics(
                duration_seconds=duration_seconds,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                failed=True,
            )
            logger.error(
                "llm_request_failed",
                extra={
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "error_type": type(exc).__name__,
                    "model": self.settings.llm_model,
                },
            )
            return "ขออภัย ระบบผู้ช่วยสั่งอาหารขัดข้องชั่วคราว กรุณาลองใหม่ภายหลัง"
        duration_seconds = perf_counter() - started_at
        self._record_llm_metrics(
            duration_seconds=duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            failed=False,
        )
        logger.info(
            "llm_request_completed",
            extra={
                "duration_ms": round(duration_seconds * 1000, 3),
                "input_tokens": input_tokens,
                "model": self.settings.llm_model,
                "output_tokens": output_tokens,
            },
        )
        self.memory.append(identity.id, "user", message)
        self.memory.append(identity.id, "assistant", final_text)
        return final_text[:5000]

    async def close(self) -> None:
        close = getattr(self.model, "close", None)
        if callable(close):
            await close()
