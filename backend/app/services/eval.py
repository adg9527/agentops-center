"""Automated evaluation loop.

A suite pins down *what* is being graded (agents + skills + prompts + MCP servers
+ cases) and *what counts as passing* (per-dimension thresholds). Running it
executes every case through the regular task pipeline, scores the runs and the
trajectories, and repeats — carrying the previous round's failures forward as
feedback — until every dimension passes or the round budget is spent.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..config import get_settings
from ..db import session_factory, utcnow
from ..models import (
    Agent,
    AgentRun,
    EvalRun,
    EvalSuite,
    McpServer,
    PromptTemplate,
    RunStatus,
    Skill,
    Task,
    TaskStatus,
    TraceEvent,
)
from .event_bus import bus
from .task_runner import runner
from .trace import new_span_id, new_trace_id

log = logging.getLogger(__name__)

CASE_TIMEOUT_SEC = 600
_STOP: set[int] = set()


def default_thresholds() -> dict:
    return {"completion": 1.0, "tool_use": 0.0, "keyword": 0.8, "max_avg_tokens": 0, "max_avg_ms": 0}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _clip(text: str | None, limit: int = 600) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …"


async def _compose_prompt(suite: EvalSuite, case: dict, hints: str) -> str:
    """Prompt = 上一轮反馈 + Prompt 模板 + 技能内容 + 用例本身。"""
    parts: list[str] = []
    if hints.strip():
        parts.append(f"## 上一轮评测未达标的地方（本轮请针对性改进）\n{hints.strip()}")
    async with session_factory()() as s:
        if suite.prompt_ids:
            tpl = await s.get(PromptTemplate, suite.prompt_ids[0])
            if tpl and not tpl.deleted:
                parts.append(tpl.content)
        skills = []
        for sid in suite.skill_ids:
            skill = await s.get(Skill, sid)
            if skill and not skill.deleted:
                skills.append(skill)
    for skill in skills:
        parts.append(f"## 技能：{skill.name}\n{_clip(skill.content, 4000)}")
    parts.append(str(case.get("prompt") or ""))
    return "\n\n".join(p for p in parts if p.strip())


async def _context_summary(suite: EvalSuite) -> dict:
    async with session_factory()() as s:
        skills = [await s.get(Skill, i) for i in suite.skill_ids or []]
        prompts = [await s.get(PromptTemplate, i) for i in suite.prompt_ids or []]
        mcps = [await s.get(McpServer, i) for i in suite.mcp_ids or []]
        agents = [await s.get(Agent, i) for i in suite.agent_ids or []]
    return {
        "skills": [x.name for x in skills if x],
        "prompts": [x.name for x in prompts if x],
        "mcps": [x.name for x in mcps if x],
        "agents": [x.name for x in agents if x],
    }


async def _run_case(suite: EvalSuite, case: dict, hints: str) -> dict:
    prompt = await _compose_prompt(suite, case, hints)
    async with session_factory()() as s:
        task = Task(
            project_id=suite.project_id,
            title=f"[eval] {str(case.get('prompt') or '')[:48]}",
            prompt=prompt,
            status=TaskStatus.pending.value,
            trace_id=new_trace_id(),
        )
        s.add(task)
        await s.flush()
        task.workspace_dir = str(get_settings().workspaces_dir / f"task-{task.id}")
        run_ids: list[int] = []
        for agent_id in suite.agent_ids or []:
            agent = await s.get(Agent, agent_id)
            if not agent or agent.deleted or not agent.enabled:
                continue
            run = AgentRun(task_id=task.id, agent_id=agent.id, agent_config_version=agent.config_version,
                           status=RunStatus.pending.value, span_id=new_span_id())
            s.add(run)
            await s.flush()
            run_ids.append(run.id)
        await s.commit()
        task_id = task.id

    if not run_ids:
        return {"task_id": task_id, "prompt": case.get("prompt"), "agents": [],
                "error": "没有可用的 Agent（未启用或已删除）"}

    runner.submit(task_id)
    try:
        await asyncio.wait_for(runner.wait(task_id), timeout=CASE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        runner.cancel(task_id)
        log.warning("eval case timed out: task %s", task_id)

    async with session_factory()() as s:
        task = await s.get(Task, task_id)
        rows = (await s.execute(
            select(AgentRun, Agent.name).join(Agent, Agent.id == AgentRun.agent_id).where(AgentRun.task_id == task_id)
        )).all()
        events = (await s.scalars(
            select(TraceEvent).where(TraceEvent.task_id == task_id, TraceEvent.event_type == "tool.call")
        )).all()

    tool_calls: dict[int, int] = {}
    for event in events:
        if event.run_id:
            tool_calls[event.run_id] = tool_calls.get(event.run_id, 0) + 1

    expect = str(case.get("expect") or "").strip()
    agents_out = []
    for run, name in rows:
        output = run.output_text or ""
        agents_out.append({
            "agent_id": run.agent_id,
            "agent": name,
            "run_id": run.id,
            "status": run.status,
            "completed": run.status == RunStatus.completed.value,
            "tool_calls": tool_calls.get(run.id, 0),
            "keyword_hit": (expect.lower() in output.lower()) if expect else None,
            "tokens": (run.input_tokens or 0) + (run.output_tokens or 0),
            "duration_ms": run.duration_ms or 0,
            "output": _clip(output),
            "error": run.error,
        })
    return {"task_id": task_id, "prompt": case.get("prompt"), "expect": expect,
            "status": task.status if task else "unknown", "agents": agents_out}


def _score_round(cases: list[dict], thresholds: dict) -> dict:
    completed, tool_used, keywords, tokens, durations = [], [], [], [], []
    for case in cases:
        for agent in case.get("agents") or []:
            completed.append(1.0 if agent["completed"] else 0.0)
            tool_used.append(1.0 if agent["tool_calls"] > 0 else 0.0)
            if agent["keyword_hit"] is not None:
                keywords.append(1.0 if agent["keyword_hit"] else 0.0)
            tokens.append(float(agent["tokens"]))
            durations.append(float(agent["duration_ms"]))
    metrics = {
        "completion": _mean(completed),
        "tool_use": _mean(tool_used),
        "keyword": _mean(keywords),
        "avg_tokens": round(sum(tokens) / len(tokens)) if tokens else 0,
        "avg_ms": round(sum(durations) / len(durations)) if durations else 0,
        "cases": len(cases),
        "runs": len(completed),
    }
    failures: list[str] = []
    if metrics["completion"] is not None and metrics["completion"] < float(thresholds.get("completion") or 0):
        failures.append(f"完成率 {metrics['completion']:.2f} < {float(thresholds.get('completion') or 0):.2f}")
    want_tool = float(thresholds.get("tool_use") or 0)
    if want_tool and (metrics["tool_use"] is None or metrics["tool_use"] < want_tool):
        failures.append(f"工具使用率 {(metrics['tool_use'] or 0):.2f} < {want_tool:.2f}")
    want_kw = float(thresholds.get("keyword") or 0)
    if want_kw and metrics["keyword"] is not None and metrics["keyword"] < want_kw:
        failures.append(f"关键词命中 {metrics['keyword']:.2f} < {want_kw:.2f}")
    max_tokens = int(thresholds.get("max_avg_tokens") or 0)
    if max_tokens and metrics["avg_tokens"] > max_tokens:
        failures.append(f"平均 token {metrics['avg_tokens']} > {max_tokens}")
    max_ms = int(thresholds.get("max_avg_ms") or 0)
    if max_ms and metrics["avg_ms"] > max_ms:
        failures.append(f"平均耗时 {metrics['avg_ms']}ms > {max_ms}ms")
    metrics["passed"] = not failures
    metrics["failures"] = failures
    return metrics


def _hints_from(cases: list[dict], metrics: dict) -> str:
    lines = [f"- {f}" for f in metrics.get("failures", [])]
    for case in cases:
        bad = [a for a in case.get("agents") or [] if not a["completed"] or (case.get("expect") and not a["keyword_hit"])]
        if bad:
            reason = bad[0].get("error") or "输出未覆盖期望内容"
            lines.append(f"- 用例「{_clip(str(case.get('prompt')), 60)}」未达标：{_clip(reason, 200)}")
    return "\n".join(lines[:8])


async def run_suite(suite_id: int) -> None:
    """Round loop: run every case, score, stop as soon as all dimensions pass."""
    async with session_factory()() as s:
        suite_obj = await s.get(EvalSuite, suite_id)
        if not suite_obj or suite_obj.deleted:
            return
        threshold_snapshot = dict(suite_obj.thresholds or default_thresholds())
        total_rounds = max(1, int(suite_obj.max_rounds or 1))
    # 上下文与用例在会话外读取，避免和下面的写事务抢 SQLite 锁
    context = await _context_summary(suite_obj)
    case_list = list(suite_obj.cases or [])

    hints = ""
    for round_no in range(1, total_rounds + 1):
        if suite_id in _STOP:
            _STOP.discard(suite_id)
            break
        async with session_factory()() as s:
            run = EvalRun(suite_id=suite_id, round=round_no, status="running", hints=hints,
                          metrics={"thresholds": threshold_snapshot, "context": context})
            s.add(run)
            await s.commit()
            run_id = run.id
        bus.publish("eval_updated", {"suite_id": suite_id, "run_id": run_id, "round": round_no, "status": "running"})

        try:
            cases = [await _run_case(suite_obj, case, hints) for case in case_list]
            metrics = _score_round(cases, threshold_snapshot)
        except Exception as exc:  # noqa: BLE001 - a broken suite must not kill the loop silently
            log.exception("eval suite %s round %s failed", suite_id, round_no)
            async with session_factory()() as s:
                row = await s.get(EvalRun, run_id)
                row.status = "failed"
                row.error = f"{type(exc).__name__}: {exc}"[:2000]
                row.finished_at = utcnow()
                await s.commit()
            bus.publish("eval_updated", {"suite_id": suite_id, "run_id": run_id, "status": "failed"})
            return

        async with session_factory()() as s:
            row = await s.get(EvalRun, run_id)
            row.status = "completed"
            row.passed = bool(metrics["passed"])
            row.metrics = {**(row.metrics or {}), **metrics}
            row.detail = cases
            row.task_ids = [c["task_id"] for c in cases if c.get("task_id")]
            row.finished_at = utcnow()
            await s.commit()
        bus.publish("eval_updated", {"suite_id": suite_id, "run_id": run_id, "round": round_no,
                                     "status": "completed", "passed": bool(metrics["passed"])})
        if metrics["passed"]:
            return
        hints = _hints_from(cases, metrics)


def stop_suite(suite_id: int) -> None:
    _STOP.add(suite_id)
