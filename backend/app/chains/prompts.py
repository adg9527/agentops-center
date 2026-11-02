"""Prompt templates for the orchestration chains.

Templates live in LangChain `ChatPromptTemplate`s (not f-strings) so they can be
rendered, tested and traced independently of the service that calls them.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

PLAN_SYSTEM = """你是多 Agent 团队的协调者。把用户任务拆成可并行/串行执行的子任务，并分配给最合适的成员。

规则：
- 步骤数 1-{max_steps}；depends_on 只填必须先完成的步骤 id，无依赖的步骤会并行执行。
- agent 必须严格使用成员名单里的名称；role 取 analyst / executor / reviewer / summarizer。
- 不要安排"汇总"步骤，平台会在最后统一汇总。
- 每个 instruction 要能让成员独立开工：写清目标、输入、产出形式。"""

PLAN_HUMAN = """## 用户任务
{request}

## 可分配成员
{roster}

## 群聊近期记录
{history}"""

SUMMARY_SYSTEM = """你是团队协调者。所有子任务已结束，请基于各成员结果向用户给出最终汇总：
先给结论，再列关键产出，最后写遗留问题与建议。不要复述过程，不要编造未出现的信息。"""

SUMMARY_HUMAN = """## 用户任务
{request}

## 各成员结果
{results}"""

PLAN_PROMPT = ChatPromptTemplate.from_messages([("system", PLAN_SYSTEM), ("human", PLAN_HUMAN)])
SUMMARY_PROMPT = ChatPromptTemplate.from_messages([("system", SUMMARY_SYSTEM), ("human", SUMMARY_HUMAN)])


def format_roster(members: list[tuple[object, object]]) -> str:
    """Render `[(member, agent), ...]` as the roster block the planner sees."""
    lines = []
    for member, agent in members:
        role = getattr(member, "role", "") or getattr(agent, "role", "") or "executor"
        tags = ", ".join(getattr(agent, "capability_tags", []) or []) or "未标注"
        desc = getattr(agent, "description", "") or ""
        lines.append(f"- {agent.name}｜角色: {role}｜能力: {tags}｜{desc}")
    return "\n".join(lines)
