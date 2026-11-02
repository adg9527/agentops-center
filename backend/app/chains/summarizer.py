"""Summariser agent: step results -> final report.

A plain `create_agent` (no tools, no structured output); the report is the last
AI message of the agent result.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.language_models import BaseChatModel

from .llm import orchestrator_model
from .prompts import SUMMARY_SYSTEM, SUMMARY_USER, format_results


def build_summarizer(model: BaseChatModel):
    return create_agent(model=model, tools=[], system_prompt=SUMMARY_SYSTEM)


def _message_text(message: Any) -> str:
    """Prefer the documented `.text` accessor, fall back to string content."""
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
    return str(content)


async def summarize_results(
    *,
    request: str,
    results: list[tuple[str, str, str]],
    model: BaseChatModel | None = None,
    callbacks: list[Any] | None = None,
    agent: Any | None = None,
) -> str:
    """`results` is `[(title, agent_name, output), ...]`; returns the report text."""
    if agent is None:
        model = model or orchestrator_model()
        if model is None:
            raise RuntimeError("未配置编排模型（AGENTOPS_ORCHESTRATOR_*），无法生成汇总")
        agent = build_summarizer(model)

    message = SUMMARY_USER.format(request=request, results=format_results(results))
    result = await agent.ainvoke({"messages": [HumanMessage(message)]}, config={"callbacks": callbacks or []})
    messages = result.get("messages") or []
    if not messages:
        raise RuntimeError("汇总 agent 没有返回任何消息")
    return _message_text(messages[-1])
