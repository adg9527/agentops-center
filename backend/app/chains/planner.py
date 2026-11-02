"""Planner chain: request + roster -> validated step DAG.

LCEL composition (`prompt | model.with_structured_output(Plan)`) instead of
"ask an agent for JSON and regex it out": the model is constrained to the schema,
and `validate_plan` still rejects references to missing members or cycles.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from .callbacks import PlatformCallbackHandler
from .llm import orchestrator_model
from .plan import PlanError, validate_plan
from .prompts import PLAN_PROMPT, format_roster
from .schemas import Plan


def build_planner(model: BaseChatModel):
    """`Runnable` that maps prompt variables to a validated plan."""
    return PLAN_PROMPT | model.with_structured_output(Plan)


async def make_plan(
    *,
    request: str,
    members: list[tuple[Any, Any]],
    history: str = "",
    model: BaseChatModel | None = None,
    max_steps: int = 12,
    callbacks: list[Any] | None = None,
) -> list[dict]:
    """Produce the orchestrator's step list, or raise `PlanError`."""
    model = model or orchestrator_model()
    if model is None:
        raise PlanError("未配置编排模型（AGENTOPS_ORCHESTRATOR_*），无法生成计划")
    chain = build_planner(model)
    plan: Plan = await chain.ainvoke(
        {"request": request, "roster": format_roster(members), "history": history or "（无）", "max_steps": max_steps},
        config={"callbacks": callbacks or [PlatformCallbackHandler()]},
    )
    name_map = {agent.name: agent.id for _member, agent in members}
    return validate_plan(list(plan.steps), name_map, max_steps)
