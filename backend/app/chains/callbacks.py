"""LangChain callback handler that projects model/tool activity into platform events.

The native adapter and the orchestration chains both pass this handler, so token
counts and tool calls are recorded the same way no matter which agent ran.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult


@dataclass
class Usage:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.duration_ms += other.duration_ms
        self.model = other.model or self.model


def _tokens(result: LLMResult) -> tuple[int, int]:
    """Best-effort token extraction across providers."""
    payload = result.llm_output or {}
    usage = payload.get("token_usage") or payload.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    if input_tokens or output_tokens:
        return input_tokens, output_tokens
    for generation_list in result.generations:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            meta = getattr(message, "usage_metadata", None) or {}
            if meta:
                return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)
    return 0, 0


class PlatformCallbackHandler(AsyncCallbackHandler):
    """Collects usage and emits adapter-shaped event dicts (`kind`, `name`, ...)."""

    def __init__(self) -> None:
        super().__init__()
        self.usage = Usage()
        self.events: list[dict[str, Any]] = []
        self._started: dict[UUID, float] = {}

    # ------------------------------------------------------------------ models

    async def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = time.monotonic()
        model = (serialized or {}).get("kwargs", {}).get("model") or (serialized or {}).get("name") or "model"
        self.usage.model = str(model)

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = int((time.monotonic() - self._started.pop(run_id, time.monotonic())) * 1000)
        input_tokens, output_tokens = _tokens(response)
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.calls += 1
        self.usage.duration_ms += elapsed
        self.events.append(
            {
                "kind": "model.call",
                "name": self.usage.model or "model",
                "status": "ok",
                "duration_ms": elapsed,
                "attributes": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            }
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._started.pop(run_id, None)
        self.events.append({"kind": "model.call", "name": self.usage.model or "model", "status": "error",
                            "output": str(error)[:2000], "attributes": {}})

    # ------------------------------------------------------------------- tools

    async def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = time.monotonic()
        name = (serialized or {}).get("name") or "tool"
        self.events.append({"kind": "tool.call", "name": str(name), "status": "ok",
                            "input": input_str[:4000], "call_id": str(run_id), "attributes": {}})

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = int((time.monotonic() - self._started.pop(run_id, time.monotonic())) * 1000)
        self.events.append({"kind": "tool.result", "name": "tool", "status": "ok",
                            "output": str(output)[:4000], "call_id": str(run_id), "duration_ms": elapsed,
                            "attributes": {}})

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._started.pop(run_id, None)
        self.events.append({"kind": "tool.result", "name": "tool", "status": "error",
                            "output": str(error)[:2000], "call_id": str(run_id), "attributes": {}})

    # ------------------------------------------------------------------- drain

    def drain(self) -> list[dict[str, Any]]:
        """Return and clear buffered events."""
        events, self.events = self.events, []
        return events
