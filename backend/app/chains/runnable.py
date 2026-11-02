"""Adapter invocation as a LangChain `Runnable`.

CLI agents, HTTP agents and native LangChain agents all execute through the same
runtime entry point, so batching/retry/config composition works uniformly and the
call site never branches on adapter type.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import Runnable, RunnableConfig

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps adapters importable without a cycle
    from ..adapters.base import AdapterEvent, AgentAdapter, Emit, RunRequest, RunResult


class AdapterRunnable(Runnable[dict, Any]):
    """Wraps `AgentAdapter.run` as a `Runnable`.

    The adapter keeps its own cancellation and streaming contract; this class only
    gives it the standard LangChain interface (`ainvoke`, `with_config`, ...).
    """

    def __init__(
        self,
        adapter: "AgentAdapter",
        request: "RunRequest",
        emit: "Emit",
        cancel: asyncio.Event,
        *,
        task_id: int | None = None,
    ) -> None:
        self.adapter = adapter
        self.request = request
        self.emit = emit
        self.cancel = cancel
        self.task_id = task_id

    def invoke(self, input: dict, config: RunnableConfig | None = None, **kwargs: Any) -> "RunResult":  # noqa: A002
        raise RuntimeError("AdapterRunnable 只支持异步调用：请使用 ainvoke")

    async def ainvoke(  # type: ignore[override]
        self, input: dict, config: RunnableConfig | None = None, **kwargs: Any
    ) -> "RunResult":
        return await self.adapter.run(self.request, self.emit, self.cancel)

    def get_name(self, suffix: str | None = None, *, name: str | None = None) -> str:
        base = f"adapter:{self.adapter.type}"
        return f"{base}-{suffix}" if suffix else base


async def run_adapter(
    adapter: "AgentAdapter",
    request: "RunRequest",
    emit: "Emit",
    cancel: asyncio.Event,
    *,
    task_id: int | None = None,
) -> "RunResult":
    """Execute one attempt through the LangChain runtime."""
    runnable = AdapterRunnable(adapter, request, emit, cancel, task_id=task_id)
    return await runnable.ainvoke({"task_id": task_id, "adapter": adapter.type})
