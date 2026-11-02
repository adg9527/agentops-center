"""Callback plumbing: platform events + official token accounting.

Token counts come from `UsageMetadataCallbackHandler` (the documented way to
track usage, https://docs.langchain.com/oss/python/langchain/models#token-usage);
this module only adds the projection into platform trace events.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler, UsageMetadataCallbackHandler


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


class PlatformCallbackHandler(AsyncCallbackHandler):
    """Projects model/tool activity into the platform's normalized event dicts."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []
        self.model: str | None = None
        self.calls = 0
        self.duration_ms = 0
        self._started: dict[UUID, float] = {}

    # ------------------------------------------------------------------ models

    async def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID, **kwargs: Any) -> None:
        self._started[run_id] = time.monotonic()
        kwargs_ = (serialized or {}).get("kwargs", {})
        self.model = str(kwargs_.get("model") or kwargs_.get("model_name") or (serialized or {}).get("name") or "model")

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = int((time.monotonic() - self._started.pop(run_id, time.monotonic())) * 1000)
        self.calls += 1
        self.duration_ms += elapsed
        self.events.append({"kind": "model.call", "name": self.model or "model", "status": "ok",
                            "duration_ms": elapsed, "attributes": {}})

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._started.pop(run_id, None)
        self.events.append({"kind": "model.call", "name": self.model or "model", "status": "error",
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

    def drain(self) -> list[dict[str, Any]]:
        """Return and clear buffered events."""
        events, self.events = self.events, []
        return events


def build_callbacks() -> list[Any]:
    """Handlers to pass as `config={"callbacks": [...]}`: events + token usage."""
    return [PlatformCallbackHandler(), UsageMetadataCallbackHandler()]


def usage_totals(callbacks: list[Any] | None = None, *,
                 events: PlatformCallbackHandler | None = None) -> Usage:
    """Aggregate the official usage metadata into a platform `Usage`."""
    usage = Usage()
    for handler in callbacks or []:
        if not isinstance(handler, UsageMetadataCallbackHandler):
            continue
        for model, meta in (handler.usage_metadata or {}).items():
            usage.model = model or usage.model
            usage.input_tokens += int(meta.get("input_tokens") or 0)
            usage.output_tokens += int(meta.get("output_tokens") or 0)
            usage.calls += 1
    if events is not None:
        usage.model = usage.model or events.model
        usage.calls = usage.calls or events.calls
        usage.duration_ms = events.duration_ms
    return usage
