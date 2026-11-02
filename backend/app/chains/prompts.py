"""System prompts and message bodies for the orchestration agents.

`create_agent` takes the system prompt at construction time, so these are plain
strings; the user message is rendered per request in `planner` / `summarizer`.
"""

from __future__ import annotations

from typing import Any

PLAN_SYSTEM = """你是多 Agent 团队的协调者。把用户任务拆成可并行/串行执行的子任务，并分配给最合适的成员。

规则：
- 步骤数 1-{max_steps}；depends_on 只填必须先完成的步骤 id，无依赖的步骤会并行执行。
- agent 必须严格使用成员名单里的名称；role 取 analyst / executor / reviewer / summarizer。
- 不要安排“汇总”步骤，平台会在最后统一汇总。
- 每个 instruction 要能让成员独立开工：写清目标、输入、产出形式。"""

PLAN_USER = """## 用户任务
{request}

## 可分配成员
{roster}

## 群聊近期记录
{history}"""

SUMMARY_SYSTEM = """你是团队协调者。所有子任务已结束，请基于各成员结果向用户给出最终汇总：
先给结论，再列关键产出，最后写遗留问题与建议。不要复述过程，不要编造未出现的信息。"""

SUMMARY_USER = """## 用户任务
{request}

## 各成员结果
{results}"""


def plan_system_prompt(max_steps: int = 12) -> str:
    return PLAN_SYSTEM.format(max_steps=max_steps)


def format_roster(members: list[tuple[Any, Any]]) -> str:
    """Render `[(member, agent), ...]` as the roster block the planner sees."""
    lines = []
    for member, agent in members:
        role = getattr(member, "role", "") or getattr(agent, "role", "") or "executor"
        tags = ", ".join(getattr(agent, "capability_tags", []) or []) or "未标注"
        desc = getattr(agent, "description", "") or ""
        lines.append(f"- {agent.name}｜角色: {role}｜能力: {tags}｜{desc}")
    return "\n".join(lines)


def _clip(text: str | None, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n…（已截断，原文 {len(text)} 字）"


def format_results(results: list[tuple[str, str, str]]) -> str:
    """Render `[(title, agent_name, output), ...]` for the summariser."""
    return "\n\n".join(f"### {title}（{agent}）\n{_clip(output)}" for title, agent, output in results)
