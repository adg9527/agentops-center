"""Summariser chain: step results -> final report."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser

from .callbacks import PlatformCallbackHandler
from .llm import orchestrator_model
from .prompts import SUMMARY_PROMPT


def build_summarizer(model: BaseChatModel):
    """`Runnable` that maps prompt variables to the report text."""
    return SUMMARY_PROMPT | model | StrOutputParser()


def _clip(text: str | None, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n…（已截断，原文 {len(text)} 字）"


async def summarize_results(
    *,
    request: str,
    results: list[tuple[str, str, str]],
    model: BaseChatModel | None = None,
    callbacks: list[Any] | None = None,
) -> str:
    """`results` is `[(title, agent_name, output), ...]`; returns the report text."""
    model = model or orchestrator_model()
    if model is None:
        raise RuntimeError("未配置编排模型（AGENTOPS_ORCHESTRATOR_*），无法生成汇总")
    body = "\n\n".join(f"### {title}（{agent}）\n{_clip(output)}" for title, agent, output in results)
    chain = build_summarizer(model)
    return await chain.ainvoke({"request": request, "results": body}, config={"callbacks": callbacks or [PlatformCallbackHandler()]})
