"""Plan validation shared by the LCEL planner chain and the legacy JSON parser.

The chain returns typed objects, but a plan can still be unusable: unknown member
names, duplicate ids, missing dependencies or cycles. Keep that judgement in one
place so both entry points reject the same things with the same messages.
"""

from __future__ import annotations

from typing import Any

ROLES = {"analyst", "executor", "reviewer", "summarizer"}


class PlanError(ValueError):
    """The plan is structurally invalid or references something that does not exist."""


def validate_plan(raw_steps: list[Any], members: dict[str, int], max_steps: int = 12) -> list[dict]:
    """Normalise steps into the dicts the orchestrator persists. Raises `PlanError`."""
    if not raw_steps:
        raise PlanError("主控没有给出任何步骤")
    if len(raw_steps) > max_steps:
        raise PlanError(f"计划步骤过多（{len(raw_steps)} > {max_steps}）")

    lowered = {k.lower(): v for k, v in members.items()}
    out: list[dict] = []
    keys: set[str] = set()
    for i, raw in enumerate(raw_steps):
        step = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw or {})
        key = str(step.get("id") or f"s{i + 1}")
        if key in keys:
            raise PlanError(f"步骤 id 重复: {key}")
        agent_name = str(step.get("agent") or "")
        agent_id = members.get(agent_name) or lowered.get(agent_name.lower())
        if not agent_id:
            raise PlanError(f"步骤 {key} 指定的成员不存在: {agent_name!r}（可选: {', '.join(members)}）")
        instruction = str(step.get("instruction") or "").strip()
        if not instruction:
            raise PlanError(f"步骤 {key} 缺少 instruction")
        role = step.get("role") if step.get("role") in ROLES else "executor"
        deps = [str(d) for d in (step.get("depends_on") or [])]
        keys.add(key)
        out.append(
            {
                "key": key,
                "agent_id": agent_id,
                "title": str(step.get("title") or instruction[:60]),
                "instruction": instruction,
                "role": role,
                "depends_on": deps,
            }
        )

    for s in out:
        missing = [d for d in s["depends_on"] if d not in keys]
        if missing:
            raise PlanError(f"步骤 {s['key']} 依赖不存在的步骤: {missing}")
    check_acyclic({s["key"]: s["depends_on"] for s in out})
    return out


def check_acyclic(graph: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise PlanError(f"计划存在循环依赖（涉及 {node}）")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep)
        state[node] = 2

    for node in graph:
        visit(node)
