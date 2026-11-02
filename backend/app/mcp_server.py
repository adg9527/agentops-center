"""MCP server exposing AgentOps Center to other AI tools (WorkBuddy, Claude Code, ...).

Two transports share one tool set:

* **Streamable HTTP** — mounted at `/mcp` by the FastAPI app, so a client that
  supports remote MCP servers can call `http://127.0.0.1:8000/mcp` with
  `Authorization: Bearer <JWT>` (obtain it from `POST /api/auth/login`).
* **stdio** — `python -m app.mcp_server` for clients that spawn a local process
  (Claude Code's `mcpServers`, WorkBuddy's MCP settings, ...).

Built on the official MCP Python SDK (`mcp>=2`, `mcp.server.mcpserver.MCPServer`).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
from typing import Any
from urllib.parse import parse_qs

from datetime import timedelta

from sqlalchemy import func, select
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings
from .db import session_factory, utcnow
from .models import Agent, AgentRun, Project, Role, RunStatus, Task, TaskStatus, TraceEvent
from .security import decode_access_token
from .services.audit import audit
from .services.task_runner import run_to_dict, runner
from .services.trace import event_to_dict, new_span_id, new_trace_id

try:
    from mcp.server.mcpserver import MCPServer
except Exception as exc:  # pragma: no cover - the package is a hard dependency, but never break import
    MCPServer = None  # type: ignore
    _MCP_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    _MCP_IMPORT_ERROR = None

_ROLE = contextvars.ContextVar("agentops_mcp_role", default="admin")
_RANK = {"viewer": 0, "operator": 1, "admin": 2}


def _require_operator() -> None:
    if _RANK.get(_ROLE.get(), 0) < _RANK["operator"]:
        raise PermissionError("当前令牌是 viewer，不能派发任务（需要 operator 及以上）")


def _task_brief(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "mode": t.mode,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "error": t.error,
    }


server = None
if MCPServer is not None:
    server = MCPServer(
        name="agentops-center",
        instructions=(
            "AgentOps Center 是 AI Agent 的统一运维与协作平台。"
            "你可以查询已接入的 Agent、查看任务与执行事件，也可以把任务派发给指定 Agent。"
            "任务在后台异步执行，用 get_task 轮询状态与结果。"
        ),
    )

    @server.tool()
    async def list_agents(only_enabled: bool = False) -> list[dict]:
        """列出平台上已接入的 Agent（名称、接入类型、模型、启停与健康状态）。"""
        async with session_factory()() as s:
            q = select(Agent).where(Agent.deleted.is_(False)).order_by(Agent.id)
            if only_enabled:
                q = q.where(Agent.enabled.is_(True))
            rows = (await s.scalars(q)).all()
            return [
                {
                    "id": a.id,
                    "name": a.name,
                    "adapter_type": a.adapter_type,
                    "platform": a.platform,
                    "model": a.model,
                    "role": a.role,
                    "enabled": a.enabled,
                    "health": a.health,
                    "max_concurrency": a.max_concurrency,
                }
                for a in rows
            ]

    @server.tool()
    async def get_agent(agent_id: int) -> dict:
        """查看单个 Agent 的详细配置（不含密钥明文）。"""
        async with session_factory()() as s:
            a = await s.get(Agent, agent_id)
            if not a or a.deleted:
                raise ValueError(f"Agent {agent_id} 不存在")
            return {
                "id": a.id,
                "name": a.name,
                "adapter_type": a.adapter_type,
                "platform": a.platform,
                "transport": a.transport,
                "model": a.model,
                "description": a.description,
                "role": a.role,
                "capability_tags": a.capability_tags,
                "config": a.config,
                "enabled": a.enabled,
                "health": a.health,
                "timeout_sec": a.timeout_sec,
                "max_retries": a.max_retries,
                "config_version": a.config_version,
            }

    @server.tool()
    async def list_tasks(status: str | None = None, limit: int = 10) -> list[dict]:
        """列出最近的顶层任务；status 可选 pending/running/completed/failed/cancelled 等。"""
        async with session_factory()() as s:
            q = select(Task).where(Task.parent_task_id.is_(None))
            if status:
                q = q.where(Task.status == status)
            rows = (await s.scalars(q.order_by(Task.id.desc()).limit(min(max(limit, 1), 50)))).all()
            return [_task_brief(t) for t in rows]

    @server.tool()
    async def get_task(task_id: int, event_limit: int = 20) -> dict:
        """查看任务详情：状态、各 Agent 的执行结果、最近的事件时间线。"""
        async with session_factory()() as s:
            t = await s.get(Task, task_id)
            if not t:
                raise ValueError(f"任务 {task_id} 不存在")
            runs = (
                await s.execute(
                    select(AgentRun, Agent.name, Agent.adapter_type)
                    .join(Agent, Agent.id == AgentRun.agent_id)
                    .where(AgentRun.task_id == t.id)
                    .order_by(AgentRun.id)
                )
            ).all()
            events = (
                await s.scalars(
                    select(TraceEvent)
                    .where(TraceEvent.trace_id == t.trace_id)
                    .order_by(TraceEvent.id.desc())
                    .limit(min(max(event_limit, 0), 100))
                )
            ).all()
            return {
                **_task_brief(t),
                "prompt": t.prompt,
                "workspace_dir": t.workspace_dir,
                "trace_id": t.trace_id,
                "runs": [
                    {
                        **run_to_dict(r),
                        "agent_name": name,
                        "adapter_type": adapter,
                    }
                    for r, name, adapter in runs
                ],
                "events": [event_to_dict(e) for e in reversed(events)],
            }

    @server.tool()
    async def create_task(prompt: str, agent_ids: list[int], title: str = "") -> dict:
        """把任务派发给一个或多个 Agent（需要 operator 及以上令牌）。任务异步执行，用 get_task 跟进。"""
        _require_operator()
        if not prompt.strip():
            raise ValueError("prompt 不能为空")
        if not agent_ids:
            raise ValueError("agent_ids 至少一个")
        async with session_factory()() as s:
            agents = (await s.scalars(select(Agent).where(Agent.id.in_(agent_ids)))).all()
            found = {a.id: a for a in agents if not a.deleted}
            missing = [i for i in agent_ids if i not in found]
            if missing:
                raise ValueError(f"Agent 不存在: {missing}")
            disabled = [a.name for a in found.values() if not a.enabled]
            if disabled:
                raise ValueError(f"Agent 已停用: {', '.join(disabled)}")
            project_id = next(iter(found.values())).project_id
            if not await s.get(Project, project_id):
                raise ValueError("项目不存在")
            task_title = title.strip() or prompt.strip().splitlines()[0][:80]
            task = Task(
                project_id=project_id,
                title=task_title,
                prompt=prompt,
                status=TaskStatus.pending.value,
                trace_id=new_trace_id(),
                created_by=None,
            )
            s.add(task)
            await s.flush()
            task.workspace_dir = str(get_settings().workspaces_dir / f"task-{task.id}")
            for aid in dict.fromkeys(agent_ids):
                a = found[aid]
                s.add(
                    AgentRun(
                        task_id=task.id,
                        agent_id=a.id,
                        agent_config_version=a.config_version,
                        status=RunStatus.pending.value,
                        span_id=new_span_id(),
                    )
                )
            await s.commit()
            task_id, brief = task.id, _task_brief(task)
        runner.submit(task_id)
        return {**brief, "dispatched": True}

    @server.tool()
    async def list_events(limit: int = 20, task_id: int | None = None) -> list[dict]:
        """列出最近的执行事件（可只看某个任务），用于快速了解平台正在发生什么。"""
        async with session_factory()() as s:
            q = select(TraceEvent).order_by(TraceEvent.id.desc()).limit(min(max(limit, 1), 100))
            if task_id is not None:
                t = await s.get(Task, task_id)
                if not t:
                    raise ValueError(f"任务 {task_id} 不存在")
                q = q.where(TraceEvent.trace_id == t.trace_id)
            rows = (await s.scalars(q)).all()
            return [event_to_dict(e) for e in rows]

    @server.tool()
    async def dashboard_summary(days: int = 7) -> dict:
        """平台概览：Agent 数、任务状态分布、近 N 天的 run 数与成本。"""
        async with session_factory()() as s:
            agents_total = await s.scalar(select(func.count(Agent.id)).where(Agent.deleted.is_(False)))
            agents_online = await s.scalar(
                select(func.count(Agent.id)).where(Agent.deleted.is_(False), Agent.health == "online")
            )
            tasks = (await s.scalars(select(Task).where(Task.parent_task_id.is_(None)))).all()
            status: dict[str, int] = {}
            for t in tasks:
                status[t.status] = status.get(t.status, 0) + 1
            since = utcnow() - timedelta(days=max(days, 1))
            runs = (await s.scalars(select(AgentRun).where(AgentRun.created_at >= since))).all()
            cost = sum(r.cost_usd or 0 for r in runs)
            return {
                "agents": {"total": agents_total or 0, "online": agents_online or 0},
                "tasks": {"total": len(tasks), "by_status": status},
                "runs": {"total": len(runs), "cost_usd": round(cost, 4)},
                "window_days": days,
            }


class _BearerAuth:
    """Require a valid platform JWT on the HTTP transport, then expose its role to tools."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = ""
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            token = (parse_qs(scope.get("query_string", b"").decode()).get("token") or [""])[0]
        payload = decode_access_token(token) if token else None
        if not payload:
            body = json.dumps({"error": "missing or invalid token"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        reset = _ROLE.set(str(payload.get("role") or "viewer"))
        try:
            await self.app(scope, receive, send)
        finally:
            _ROLE.reset(reset)


@contextlib.asynccontextmanager
async def mcp_session_manager():
    """Start the Streamable HTTP session manager for the app's lifetime.

    A mounted Starlette app does not receive lifespan events from FastAPI, so the
    MCP transport has to be started from the host app's lifespan.
    """
    if server is None:
        yield
        return
    async with server.session_manager.run():
        yield


def build_asgi_app() -> ASGIApp | None:
    """Starlette app for the Streamable HTTP transport, wrapped with JWT auth."""
    if server is None:
        return None
    inner = server.streamable_http_app(streamable_http_path="/", host="127.0.0.1")
    return _BearerAuth(inner)


def run_stdio() -> None:
    """Serve the same tools over stdio (for clients that spawn a local process)."""
    if server is None:
        raise SystemExit(f"未安装 MCP SDK：{_MCP_IMPORT_ERROR}")
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover - manual entry point
    run_stdio()
