"""Planner agent: request + roster -> validated step DAG.

Built the documented way — `create_agent(..., response_format=Plan)` — so the
model is constrained to the schema and LangChain handles validation retries
(https://docs.langchain.com/oss/python/langchain/structured-output).
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.language_models import BaseChatModel

from .llm import orchestrator_model
from .plan import PlanError, validate_plan
from .prompts import PLAN_USER, format_roster, plan_system_prompt
from .schemas import Plan


def build_planner(model: BaseChatModel, *, max_steps: int = 12):
    """Agent whose final state carries a `Plan` under `structured_response`."""
    return create_agent(
        model=model,
        tools=[],
        system_prompt=plan_system_prompt(max_steps),
        response_format=Plan,
    )


async def make_plan(
    *,
    request: str,
    members: list[tuple[Any, Any]],
    history: str = "",
    model: BaseChatModel | None = None,
    max_steps: int = 12,
    callbacks: list[Any] | None = None,
    agent: Any | None = None,
) -> list[dict]:
    """Produce the orchestrator's step list, or raise `PlanError`."""
    if agent is None:
        model = model or orchestrator_model()
        if model is None:
            raise PlanError("未配置编排模型（AGENTOPS_ORCHESTRATOR_*），无法生成计划")
        agent = build_planner(model, max_steps=max_steps)

    message = PLAN_USER.format(request=request, roster=format_roster(members), history=history or "（无）")
    result = await agent.ainvoke({"messages": [HumanMessage(message)]}, config={"callbacks": callbacks or []})

    plan = result.get("structured_response")
    steps = getattr(plan, "steps", None)
    if steps is None and isinstance(plan, dict):
        steps = plan.get("steps")
    if not steps:
        raise PlanError("模型没有返回结构化计划（structured_response 为空）")

    name_map = {member_agent.name: member_agent.id for _member, member_agent in members}
    return validate_plan(list(steps), name_map, max_steps)
