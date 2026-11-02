"""Graph projections for the workbench: task relationship graph + participant mind map.

Two read-only views over data the platform already stores:

* `task_graph`   — who got what, what depends on what, and how far along it is
                   (tasks → steps → agents, plus dependency/handoff edges).
* `task_mindmap` — the participant hierarchy for one task: participants → tools
                   they called → files they touched (derived from trace events).
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentRun, Handoff, Task, TaskStep, TraceEvent

_PATH_KEYS = {"path", "file", "file_path", "filepath", "filename", "target_file", "notebook_path", "cwd"}
_PATH_RE = re.compile(r"[A-Za-z0-9_.\-/\\]{3,180}\.(?:py|ts|tsx|js|jsx|vue|md|json|ya?ml|toml|txt|sql|html|css|sh|ps1)")

STEP_DONE = {"completed"}
STEP_BAD = {"failed", "cancelled", "skipped"}


def _task_node(task: Task) -> dict:
    return {
        "id": f"task:{task.id}",
        "label": task.title or f"任务 #{task.id}",
        "type": "task",
        "status": task.status,
        "task_id": task.id,
        "mode": task.mode,
    }


def _extract_paths(raw: str | None) -> list[str]:
    """Pull file paths out of a tool-call payload without assuming a schema."""
    if not raw:
        return []
    found: list[str] = []
    text = raw.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, str) and key.lower() in _PATH_KEYS:
                    found.append(value)
                elif isinstance(value, list):
                    found.extend(v for v in value if isinstance(v, str) and key.lower() in _PATH_KEYS)
    if not found:
        found = _PATH_RE.findall(text)
    cleaned = []
    for item in found:
        item = item.strip().strip('"').replace("\\", "/")
        if item and item not in cleaned:
            cleaned.append(item[:180])
    return cleaned[:20]


async def task_graph(session: AsyncSession, task_id: int) -> dict:
    """Nodes/edges describing progress and delegation for one task."""
    task = await session.get(Task, task_id)
    if not task:
        raise LookupError(f"任务 {task_id} 不存在")

    nodes: list[dict] = [_task_node(task)]
    edges: list[dict] = []

    steps = (
        await session.scalars(
            select(TaskStep).where(TaskStep.root_task_id == task.id).order_by(TaskStep.seq, TaskStep.id)
        )
    ).all()
    runs = (await session.execute(select(AgentRun, Agent).join(Agent, Agent.id == AgentRun.agent_id).where(AgentRun.task_id == task.id))).all()
    handoffs = (await session.scalars(select(Handoff).where(Handoff.root_task_id == task.id))).all()
    agent_names = {a.id: a.name for a in (await session.scalars(select(Agent))).all()}

    if steps:
        step_by_key = {s.step_key: s for s in steps}
        agent_ids = {s.agent_id for s in steps}
        for agent_id in sorted(agent_ids):
            name = agent_names.get(agent_id, f"Agent {agent_id}")
            done = sum(1 for s in steps if s.agent_id == agent_id and s.status in STEP_DONE)
            total = sum(1 for s in steps if s.agent_id == agent_id)
            nodes.append({
                "id": f"agent:{agent_id}",
                "label": name,
                "type": "agent",
                "agent_id": agent_id,
                "progress": round(done / total, 2) if total else 0,
                "total": total,
                "done": done,
            })
        for s in steps:
            nodes.append({
                "id": f"step:{s.step_key}",
                "label": s.title,
                "type": "step",
                "status": s.status,
                "role": s.role,
                "kind": s.kind,
                "agent_id": s.agent_id,
                "step_key": s.step_key,
                "origin": s.origin,
            })
            edges.append({"source": f"task:{task.id}", "target": f"step:{s.step_key}", "kind": "assign",
                          "label": s.step_key})
            if s.agent_id:
                edges.append({"source": f"agent:{s.agent_id}", "target": f"step:{s.step_key}", "kind": "owns",
                              "label": s.role})
            for dep in s.depends_on or []:
                if dep in step_by_key:
                    edges.append({"source": f"step:{dep}", "target": f"step:{s.step_key}", "kind": "depends",
                                  "label": "依赖"})
        for h in handoffs:
            if h.from_step_id and h.to_step_id:
                src = next((f"step:{s.step_key}" for s in steps if s.id == h.from_step_id), None)
                dst = next((f"step:{s.step_key}" for s in steps if s.id == h.to_step_id), None)
                if src and dst:
                    edges.append({"source": src, "target": dst, "kind": "handoff", "label": "交接",
                                  "status": h.status, "reason": (h.reason or "")[:120]})
    else:
        # A plain (non-collaborative) task: task → agents that ran it.
        for run, agent in runs:
            node_id = f"agent:{agent.id}"
            if not any(n["id"] == node_id for n in nodes):
                nodes.append({"id": node_id, "label": agent.name, "type": "agent", "agent_id": agent.id,
                              "progress": 1 if run.status == "completed" else 0, "total": 1,
                              "done": 1 if run.status == "completed" else 0})
            edges.append({"source": f"task:{task.id}", "target": node_id, "kind": "assign",
                          "label": f"#{run.id} {run.status}"})

    total = len(steps) or len(runs)
    done = sum(1 for s in steps if s.status in STEP_DONE) if steps else sum(
        1 for r, _a in runs if r.status == "completed")
    running = sum(1 for s in steps if s.status in ("running", "waiting_approval", "waiting_handoff")) if steps else 0
    failed = sum(1 for s in steps if s.status in STEP_BAD) if steps else 0

    return {
        "task": {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "mode": task.mode,
            "conversation_id": task.conversation_id,
        },
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total": total,
            "done": done,
            "running": running,
            "failed": failed,
            "progress": round(done / total, 3) if total else 0.0,
        },
    }


async def task_mindmap(session: AsyncSession, task_id: int) -> dict:
    """Participant hierarchy: task → participants → tools → files."""
    task = await session.get(Task, task_id)
    if not task:
        raise LookupError(f"任务 {task_id} 不存在")

    events = (
        await session.scalars(
            select(TraceEvent)
            .where(TraceEvent.trace_id == task.trace_id, TraceEvent.event_type.in_(["tool.call", "file.change"]))
            .order_by(TraceEvent.id)
        )
    ).all()

    steps = (
        await session.scalars(select(TaskStep).where(TaskStep.root_task_id == task.id).order_by(TaskStep.seq))
    ).all()
    runs = (await session.execute(select(AgentRun).where(AgentRun.task_id == task.id))).all()
    agent_ids = {s.agent_id for s in steps} | {r.agent_id for r, in runs} | {e.agent_id for e in events if e.agent_id}
    agents = {a.id: a for a in (await session.scalars(select(Agent).where(Agent.id.in_(agent_ids)))).all()} if agent_ids else {}

    per_agent: dict[int, dict] = {}
    for agent_id in agent_ids:
        if not agent_id:
            continue
        roles = sorted({s.role for s in steps if s.agent_id == agent_id})
        per_agent[agent_id] = {
            "name": agents[agent_id].name if agent_id in agents else f"Agent {agent_id}",
            "roles": roles,
            "statuses": [s.status for s in steps if s.agent_id == agent_id],
            "tools": {},
            "files": {},
        }

    for event in events:
        agent_id = event.agent_id
        if not agent_id:
            continue
        bucket = per_agent.setdefault(agent_id, {"name": f"Agent {agent_id}", "roles": [], "statuses": [],
                                                 "tools": {}, "files": {}})
        if event.event_type == "tool.call":
            name = event.name or "tool"
            bucket["tools"][name] = bucket["tools"].get(name, 0) + 1
            for path in _extract_paths(event.input_summary):
                bucket["files"][path] = bucket["files"].get(path, 0) + 1
        else:  # file.change
            path = (event.attributes or {}).get("path") or event.name or ""
            if path:
                bucket["files"][path] = bucket["files"].get(path, 0) + 1

    children = []
    for agent_id, bucket in sorted(per_agent.items()):
        tool_children = [
            {"name": f"{name} ×{count}", "type": "tool", "value": count}
            for name, count in sorted(bucket["tools"].items(), key=lambda kv: -kv[1])
        ]
        file_children = [
            {"name": path, "type": "file", "value": count}
            for path, count in sorted(bucket["files"].items(), key=lambda kv: -kv[1])[:20]
        ]
        done = sum(1 for s in bucket["statuses"] if s == "completed")
        fallback = agents[agent_id].role if agent_id in agents and agents[agent_id].role else "participant"
        role = " / ".join(bucket["roles"]) or fallback
        children.append({
            "name": f"{bucket['name']}（{role}）",
            "type": "agent",
            "agent_id": agent_id,
            "value": len(bucket["statuses"]) or 1,
            "status": f"{done}/{len(bucket['statuses'])} 步完成" if bucket["statuses"] else "",
            "children": [
                {"name": f"工具（{len(tool_children)}）", "type": "tool-group", "collapsed": False,
                 "children": tool_children or [{"name": "未调用工具", "type": "empty"}]},
                {"name": f"文件（{len(file_children)}）", "type": "file-group", "collapsed": True,
                 "children": file_children or [{"name": "未涉及文件", "type": "empty"}]},
            ],
        })

    return {
        "name": task.title or f"任务 #{task.id}",
        "type": "task",
        "task_id": task.id,
        "status": task.status,
        "children": children or [{"name": "暂无参与者", "type": "empty"}],
    }
