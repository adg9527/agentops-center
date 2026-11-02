from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import TERMINAL_TASK, Agent, AgentRun, Project, Role, RunStatus, Task, TaskStatus, TraceEvent, User
from ..services.audit import audit
from ..services.collab import orchestrator
from ..services.task_runner import run_to_dict, runner
from ..services.trace import event_to_dict, new_span_id, new_trace_id
from .deps import current_user, require

router = APIRouter(prefix="/api", tags=["tasks"])


class TaskIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    title: str = Field("", max_length=256)
    agent_ids: list[int] = Field(min_length=1, max_length=16)
    project_id: int | None = None


def task_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "title": t.title,
        "prompt": t.prompt,
        "mode": t.mode,
        "status": t.status,
        "parent_task_id": t.parent_task_id,
        "conversation_id": t.conversation_id,
        "trace_id": t.trace_id,
        "workspace_dir": t.workspace_dir,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "error": t.error,
    }


async def _runs_with_agents(session: AsyncSession, task_ids: list[int]) -> dict[int, list[dict]]:
    if not task_ids:
        return {}
    # Collaborative roots have no runs of their own; roll up their subtasks' runs.
    owner = {tid: tid for tid in task_ids}
    for sid, pid in (await session.execute(select(Task.id, Task.parent_task_id).where(Task.parent_task_id.in_(task_ids)))).all():
        owner[sid] = pid
    rows = (
        await session.execute(
            select(AgentRun, Agent.name, Agent.adapter_type)
            .join(Agent, Agent.id == AgentRun.agent_id)
            .where(AgentRun.task_id.in_(list(owner)))
            .order_by(AgentRun.id)
        )
    ).all()
    out: dict[int, list[dict]] = {}
    for r, name, at in rows:
        out.setdefault(owner[r.task_id], []).append({**run_to_dict(r), "agent_name": name, "adapter_type": at})
    return out


@router.post("/tasks")
async def create_task(body: TaskIn, request: Request, user: User = Depends(require(Role.operator)),
                      session: AsyncSession = Depends(get_session)):
    agents = (await session.scalars(select(Agent).where(Agent.id.in_(body.agent_ids)))).all()
    found = {a.id: a for a in agents if not a.deleted}
    missing = [i for i in body.agent_ids if i not in found]
    if missing:
        raise HTTPException(422, f"Agent 不存在: {missing}")
    disabled = [a.name for a in found.values() if not a.enabled]
    if disabled:
        raise HTTPException(422, f"Agent 已停用，不能接收任务: {', '.join(disabled)}")
    project_id = body.project_id or next(iter(found.values())).project_id
    if not await session.get(Project, project_id):
        raise HTTPException(422, "项目不存在")
    title = body.title.strip() or body.prompt.strip().splitlines()[0][:80]
    task = Task(project_id=project_id, title=title, prompt=body.prompt, status=TaskStatus.pending.value,
                trace_id=new_trace_id(), created_by=user.id)
    session.add(task)
    await session.flush()
    task.workspace_dir = str(get_settings().workspaces_dir / f"task-{task.id}")
    for aid in dict.fromkeys(body.agent_ids):
        a = found[aid]
        session.add(AgentRun(task_id=task.id, agent_id=a.id, agent_config_version=a.config_version,
                             status=RunStatus.pending.value, span_id=new_span_id()))
    await audit(session, "task.create", user=user, target_type="task", target_id=task.id, request=request,
                detail={"agents": body.agent_ids, "title": title})
    runner.submit(task.id)
    return task_dict(task)


@router.get("/tasks")
async def list_tasks(status: str | None = None, agent_id: int | None = None, project_id: int | None = None,
                     conversation_id: int | None = None, include_subtasks: bool = False, mode: str | None = None,
                     limit: int = 50, offset: int = 0, _: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    q = select(Task)
    if not include_subtasks:
        q = q.where(Task.parent_task_id.is_(None))
    if conversation_id:
        q = q.where(Task.conversation_id == conversation_id)
    if mode:
        q = q.where(Task.mode == mode)
    if status:
        q = q.where(Task.status == status)
    if project_id:
        q = q.where(Task.project_id == project_id)
    if agent_id:
        ran = select(AgentRun.task_id).where(AgentRun.agent_id == agent_id)
        q = q.where(Task.id.in_(ran) | Task.id.in_(select(Task.parent_task_id).where(Task.id.in_(ran))))
    total = await session.scalar(select(func.count()).select_from(q.subquery()))
    tasks = (await session.scalars(q.order_by(Task.id.desc()).limit(min(limit, 200)).offset(offset))).all()
    runs = await _runs_with_agents(session, [t.id for t in tasks])
    return {"total": total, "items": [{**task_dict(t), "runs": runs.get(t.id, [])} for t in tasks]}


async def _get_task(session: AsyncSession, task_id: int) -> Task:
    t = await session.get(Task, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    t = await _get_task(session, task_id)
    runs = await _runs_with_agents(session, [t.id])
    active = runner.is_active(t.id) or orchestrator.is_active(t.id)
    return {**task_dict(t), "runs": runs.get(t.id, []), "active": active}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, request: Request, user: User = Depends(require(Role.operator)),
                      session: AsyncSession = Depends(get_session)):
    t = await _get_task(session, task_id)
    if TaskStatus(t.status) in TERMINAL_TASK:
        raise HTTPException(409, f"任务已结束（{t.status}）")
    if orchestrator.is_active(task_id):
        orchestrator.cancel(task_id)
    elif not runner.cancel(task_id):
        raise HTTPException(409, "任务不在执行队列中")
    await audit(session, "task.cancel", user=user, target_type="task", target_id=t.id, request=request)
    return {"ok": True}


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await _get_task(session, task_id)
    rows = (await session.scalars(select(TraceEvent).where(TraceEvent.task_id == task_id).order_by(TraceEvent.id))).all()
    return [event_to_dict(e) for e in rows]


@router.get("/events")
async def list_events(task_id: int | None = None, agent_id: int | None = None, event_type: str | None = None,
                      since: datetime | None = None, until: datetime | None = None, limit: int = 200,
                      _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    q = select(TraceEvent)
    if task_id:
        q = q.where(TraceEvent.task_id == task_id)
    if agent_id:
        q = q.where(TraceEvent.agent_id == agent_id)
    if event_type:
        q = q.where(TraceEvent.event_type.startswith(event_type))
    if since:
        q = q.where(TraceEvent.timestamp >= since)
    if until:
        q = q.where(TraceEvent.timestamp <= until)
    rows = (await session.scalars(q.order_by(TraceEvent.id.desc()).limit(min(limit, 1000)))).all()
    return [event_to_dict(e) for e in rows]
