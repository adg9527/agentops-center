"""Dashboard, projects, audit log and the live WebSocket feed."""

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session, session_factory, utcnow
from ..models import Agent, AgentRun, AuditLog, Project, Role, Task, User
from ..services.audit import audit
from ..services.event_bus import bus
from .deps import current_user, require, user_from_token

router = APIRouter(prefix="/api", tags=["system"])

# Modules not built yet are reported explicitly instead of showing fabricated zeros.
_NOT_YET = {
    "mcp_count": 4,
    "skill_count": 4,
    "prompt_count": 4,
    "recent_evaluations": 5,
    "recent_reports": 6,
}


@router.get("/dashboard/summary")
async def dashboard(days: int = 7, project_id: int | None = None, agent_id: int | None = None,
                    _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    aq = select(Agent).where(Agent.deleted.is_(False))
    if project_id:
        aq = aq.where(Agent.project_id == project_id)
    agents = (await session.scalars(aq)).all()

    rq = select(AgentRun).join(Task, Task.id == AgentRun.task_id).where(AgentRun.created_at >= since)
    tq = select(Task).where(Task.created_at >= since, Task.parent_task_id.is_(None))
    if project_id:
        rq = rq.where(Task.project_id == project_id)
        tq = tq.where(Task.project_id == project_id)
    if agent_id:
        rq = rq.where(AgentRun.agent_id == agent_id)
        sub = select(Task.parent_task_id).where(Task.id.in_(select(AgentRun.task_id).where(AgentRun.agent_id == agent_id)))
        tq = tq.where(Task.id.in_(select(AgentRun.task_id).where(AgentRun.agent_id == agent_id)) | Task.id.in_(sub))
    runs = (await session.scalars(rq)).all()
    tasks = (await session.scalars(tq)).all()

    task_status: dict[str, int] = {}
    for t in tasks:
        task_status[t.status] = task_status.get(t.status, 0) + 1
    terminal_runs = [r for r in runs if r.status not in ("pending", "running")]
    ok_runs = [r for r in terminal_runs if r.status == "completed"]
    durations = [r.duration_ms for r in terminal_runs if r.duration_ms is not None]

    daily: dict[str, dict[str, int]] = {}
    for r in runs:
        d = daily.setdefault(r.created_at.date().isoformat(), {"completed": 0, "failed": 0, "other": 0})
        key = r.status if r.status in ("completed",) else ("failed" if r.status in ("failed", "timeout") else "other")
        d[key] += 1

    per_agent: dict[int, dict] = {}
    names = {a.id: a.name for a in agents}
    for r in runs:
        pa = per_agent.setdefault(r.agent_id, {"agent_id": r.agent_id, "name": names.get(r.agent_id, f"#{r.agent_id}"),
                                               "total": 0, "completed": 0})
        pa["total"] += 1
        pa["completed"] += r.status == "completed"

    recent_tasks = (await session.scalars(tq.order_by(Task.id.desc()).limit(8))).all()
    return {
        "range_days": days,
        "agents_total": len(agents),
        "agents_enabled": sum(a.enabled for a in agents),
        "agents_online": sum(a.enabled and a.health == "online" for a in agents),
        "tasks_by_status": task_status,
        "tasks_running": task_status.get("running", 0) + task_status.get("pending", 0),
        "tasks_completed": task_status.get("completed", 0),
        "tasks_failed": task_status.get("failed", 0),
        "runs_total": len(runs),
        "run_success_rate": (len(ok_runs) / len(terminal_runs)) if terminal_runs else None,
        "avg_duration_ms": (sum(durations) / len(durations)) if durations else None,
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "cost_usd": sum(r.cost_usd or 0 for r in runs),
        "cost_known_runs": sum(r.cost_usd is not None for r in runs),
        "daily_runs": [{"date": d, **v} for d, v in sorted(daily.items())],
        "per_agent": sorted(per_agent.values(), key=lambda x: -x["total"]),
        "recent_tasks": [{"id": t.id, "title": t.title, "status": t.status,
                          "created_at": t.created_at.isoformat()} for t in recent_tasks],
        "not_implemented": {k: {"phase": v} for k, v in _NOT_YET.items()},
    }


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


@router.get("/projects")
async def list_projects(_: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return [{"id": p.id, "name": p.name, "description": p.description}
            for p in (await session.scalars(select(Project).order_by(Project.id))).all()]


@router.post("/projects")
async def create_project(body: ProjectIn, request: Request, user: User = Depends(require(Role.admin)),
                         session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(Project).where(Project.name == body.name)):
        raise HTTPException(409, "项目名已存在")
    p = Project(name=body.name, description=body.description)
    session.add(p)
    await session.flush()
    await audit(session, "project.create", user=user, target_type="project", target_id=p.id, request=request,
                detail={"name": p.name})
    return {"id": p.id, "name": p.name, "description": p.description}


@router.get("/audit-logs")
async def audit_logs(action: str | None = None, limit: int = 100, offset: int = 0,
                     _: User = Depends(require(Role.admin)), session: AsyncSession = Depends(get_session)):
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    total = await session.scalar(select(func.count()).select_from(q.subquery()))
    rows = (await session.scalars(q.order_by(AuditLog.id.desc()).limit(min(limit, 500)).offset(offset))).all()
    return {"total": total, "items": [
        {"id": r.id, "username": r.username, "action": r.action, "target_type": r.target_type,
         "target_id": r.target_id, "detail": r.detail, "ip": r.ip, "created_at": r.created_at.isoformat()}
        for r in rows]}


@router.websocket("/ws")
async def ws(websocket: WebSocket, token: str = ""):
    async with session_factory()() as s:
        user = await user_from_token(token, s)
    if not user:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    q = bus.subscribe()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
            except TimeoutError:
                msg = {"topic": "ping", "data": {}}
            await websocket.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)
