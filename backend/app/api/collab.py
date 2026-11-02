"""Group chat + collaborative task API (phase 2)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session, utcnow
from ..models import (
    Agent, Approval, Conversation, ConversationMember, Handoff, MemberRole, Message, Project, Role, StepStatus, Task,
    TaskStatus, TaskStep, TERMINAL_TASK, User,
)
from ..services.audit import audit
from ..services.event_bus import bus
from ..services.collab import manual_steps, message_dict, notify, orchestrator, parse_mentions, post_message
from ..services.trace import new_trace_id
from .agents import risk_warnings
from .deps import current_user, require

router = APIRouter(prefix="/api", tags=["collab"])

RoleLit = Literal["analyst", "executor", "reviewer", "summarizer"]


# ------------------------------------------------------------------ serializers


def conv_dict(c: Conversation, members: list[tuple[ConversationMember, Agent]] | None = None, **extra) -> dict:
    d = {"id": c.id, "project_id": c.project_id, "name": c.name, "coordinator_agent_id": c.coordinator_agent_id,
         "require_plan_approval": c.require_plan_approval, "archived": c.archived,
         "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat() if c.updated_at else None, **extra}
    if members is not None:
        d["members"] = [{"id": m.id, "agent_id": a.id, "name": a.name, "role": m.role, "adapter_name": a.adapter_type,
                         "enabled": a.enabled, "health": a.health, "model": a.model, "capability_tags": a.capability_tags,
                         "risk_warnings": risk_warnings(a.adapter_type, a.config)} for m, a in members]
    return d


def step_dict(st: TaskStep, agent_name: str = "") -> dict:
    return {"id": st.id, "root_task_id": st.root_task_id, "subtask_id": st.subtask_id, "step_key": st.step_key, "kind": st.kind,
            "title": st.title, "instruction": st.instruction, "role": st.role, "agent_id": st.agent_id, "agent_name": agent_name,
            "depends_on": st.depends_on, "status": st.status, "requires_approval": st.requires_approval, "approved": st.approved,
            "origin": st.origin, "output_text": st.output_text, "error": st.error,
            "started_at": st.started_at.isoformat() if st.started_at else None,
            "finished_at": st.finished_at.isoformat() if st.finished_at else None}


def handoff_dict(h: Handoff, names: dict[int, str]) -> dict:
    return {"id": h.id, "root_task_id": h.root_task_id, "from_step_id": h.from_step_id, "to_step_id": h.to_step_id,
            "from_agent_id": h.from_agent_id, "from_agent": names.get(h.from_agent_id, "用户") if h.from_agent_id else "用户",
            "to_agent_id": h.to_agent_id, "to_agent": names.get(h.to_agent_id, ""), "initiated_by": h.initiated_by,
            "reason": h.reason, "context": h.context, "done_work": h.done_work, "remaining_work": h.remaining_work,
            "files": h.files, "result": h.result, "status": h.status, "created_at": h.created_at.isoformat(),
            "accepted_at": h.accepted_at.isoformat() if h.accepted_at else None,
            "completed_at": h.completed_at.isoformat() if h.completed_at else None}


def approval_dict(a: Approval) -> dict:
    return {"id": a.id, "root_task_id": a.root_task_id, "step_id": a.step_id, "kind": a.kind, "title": a.title,
            "detail": a.detail, "status": a.status, "decided_by": a.decided_by, "comment": a.comment,
            "decided_at": a.decided_at.isoformat() if a.decided_at else None, "created_at": a.created_at.isoformat()}


async def _members(s: AsyncSession, cid: int):
    return list((await s.execute(
        select(ConversationMember, Agent).join(Agent, Agent.id == ConversationMember.agent_id)
        .where(ConversationMember.conversation_id == cid, Agent.deleted.is_(False)).order_by(ConversationMember.id)
    )).all())


async def _conv(s: AsyncSession, cid: int) -> Conversation:
    c = await s.get(Conversation, cid)
    if not c or c.archived:
        raise HTTPException(404, "群聊不存在")
    return c


async def _names(s: AsyncSession) -> dict[int, str]:
    return dict((await s.execute(select(Agent.id, Agent.name))).all())


# ------------------------------------------------------------------ conversations


class ConvIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    project_id: int | None = None
    agent_ids: list[int] = []
    coordinator_agent_id: int | None = None
    require_plan_approval: bool = False


class ConvPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    coordinator_agent_id: int | None = None
    clear_coordinator: bool = False
    require_plan_approval: bool | None = None


@router.get("/conversations")
async def list_conversations(project_id: int | None = None, _: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    q = select(Conversation).where(Conversation.archived.is_(False)).order_by(Conversation.updated_at.desc())
    if project_id:
        q = q.where(Conversation.project_id == project_id)
    convs = (await s.scalars(q)).all()
    out = []
    for c in convs:
        last = await s.scalar(select(Message).where(Message.conversation_id == c.id).order_by(Message.id.desc()).limit(1))
        n = await s.scalar(select(func.count(ConversationMember.id)).where(ConversationMember.conversation_id == c.id))
        active = await s.scalar(select(func.count(Task.id)).where(
            Task.conversation_id == c.id, Task.parent_task_id.is_(None),
            Task.status.in_([st.value for st in TaskStatus if st not in TERMINAL_TASK])))
        out.append(conv_dict(c, member_count=n, active_tasks=active,
                             last_message=({"sender": last.sender_name, "content": last.content[:80], "at": last.created_at.isoformat()} if last else None)))
    return out


@router.post("/conversations")
async def create_conversation(body: ConvIn, request: Request, user: User = Depends(require(Role.operator)),
                              s: AsyncSession = Depends(get_session)):
    project_id = body.project_id or await s.scalar(select(func.min(Project.id)))
    if not project_id:
        raise HTTPException(422, "项目不存在")
    c = Conversation(project_id=project_id, name=body.name, require_plan_approval=body.require_plan_approval, created_by=user.id)
    s.add(c)
    await s.flush()
    for aid in dict.fromkeys(body.agent_ids):
        a = await s.get(Agent, aid)
        if a and not a.deleted:
            s.add(ConversationMember(conversation_id=c.id, agent_id=aid, role=MemberRole.executor.value))
    if body.coordinator_agent_id:
        if body.coordinator_agent_id not in body.agent_ids:
            raise HTTPException(422, "主控 Agent 必须是群成员")
        c.coordinator_agent_id = body.coordinator_agent_id
    await audit(s, "conversation.create", user=user, target_type="conversation", target_id=c.id, request=request,
                detail={"name": c.name, "agents": body.agent_ids})
    await post_message(c.id, f"{user.username} 创建了群聊「{c.name}」")
    return conv_dict(c, await _members(s, c.id))


@router.get("/conversations/{cid}")
async def get_conversation(cid: int, _: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    c = await _conv(s, cid)
    return conv_dict(c, await _members(s, cid))


@router.patch("/conversations/{cid}")
async def patch_conversation(cid: int, body: ConvPatch, request: Request, user: User = Depends(require(Role.operator)),
                             s: AsyncSession = Depends(get_session)):
    c = await _conv(s, cid)
    members = await _members(s, cid)
    if body.name is not None:
        c.name = body.name
    if body.clear_coordinator:
        c.coordinator_agent_id = None
    elif body.coordinator_agent_id is not None:
        if body.coordinator_agent_id not in [a.id for _, a in members]:
            raise HTTPException(422, "主控 Agent 必须是群成员")
        c.coordinator_agent_id = body.coordinator_agent_id
    if body.require_plan_approval is not None:
        c.require_plan_approval = body.require_plan_approval
    c.updated_at = utcnow()
    await audit(s, "conversation.update", user=user, target_type="conversation", target_id=c.id, request=request,
                detail=body.model_dump(exclude_none=True))
    bus_notify_conv(c.id)
    return conv_dict(c, members)


@router.delete("/conversations/{cid}")
async def archive_conversation(cid: int, request: Request, user: User = Depends(require(Role.operator)),
                               s: AsyncSession = Depends(get_session)):
    c = await _conv(s, cid)
    c.archived = True
    await audit(s, "conversation.archive", user=user, target_type="conversation", target_id=c.id, request=request)
    return {"ok": True}


def bus_notify_conv(cid: int):
    bus.publish("conversation_updated", {"id": cid})


class MemberIn(BaseModel):
    agent_id: int
    role: RoleLit = "executor"


@router.post("/conversations/{cid}/members")
async def add_member(cid: int, body: MemberIn, request: Request, user: User = Depends(require(Role.operator)),
                     s: AsyncSession = Depends(get_session)):
    c = await _conv(s, cid)
    a = await s.get(Agent, body.agent_id)
    if not a or a.deleted:
        raise HTTPException(404, "Agent 不存在")
    if await s.scalar(select(ConversationMember).where(ConversationMember.conversation_id == cid, ConversationMember.agent_id == a.id)):
        raise HTTPException(409, "已在群聊中")
    s.add(ConversationMember(conversation_id=cid, agent_id=a.id, role=body.role))
    c.updated_at = utcnow()
    await audit(s, "conversation.member_add", user=user, target_type="conversation", target_id=cid, request=request,
                detail={"agent_id": a.id, "role": body.role})
    await post_message(cid, f"{user.username} 邀请 {a.name} 加入群聊（角色：{body.role}）")
    bus_notify_conv(cid)
    return conv_dict(c, await _members(s, cid))


class MemberPatch(BaseModel):
    role: RoleLit


@router.patch("/conversations/{cid}/members/{agent_id}")
async def set_member_role(cid: int, agent_id: int, body: MemberPatch, request: Request, user: User = Depends(require(Role.operator)),
                          s: AsyncSession = Depends(get_session)):
    c = await _conv(s, cid)
    m = await s.scalar(select(ConversationMember).where(ConversationMember.conversation_id == cid, ConversationMember.agent_id == agent_id))
    if not m:
        raise HTTPException(404, "不是群成员")
    m.role = body.role
    await audit(s, "conversation.member_role", user=user, target_type="conversation", target_id=cid, request=request,
                detail={"agent_id": agent_id, "role": body.role})
    bus_notify_conv(cid)
    return conv_dict(c, await _members(s, cid))


@router.delete("/conversations/{cid}/members/{agent_id}")
async def remove_member(cid: int, agent_id: int, request: Request, user: User = Depends(require(Role.operator)),
                        s: AsyncSession = Depends(get_session)):
    c = await _conv(s, cid)
    m = await s.scalar(select(ConversationMember).where(ConversationMember.conversation_id == cid, ConversationMember.agent_id == agent_id))
    if not m:
        raise HTTPException(404, "不是群成员")
    await s.delete(m)
    if c.coordinator_agent_id == agent_id:
        c.coordinator_agent_id = None
    name = (await s.get(Agent, agent_id)).name
    await audit(s, "conversation.member_remove", user=user, target_type="conversation", target_id=cid, request=request,
                detail={"agent_id": agent_id})
    await post_message(cid, f"{user.username} 将 {name} 移出群聊")
    bus_notify_conv(cid)
    return conv_dict(c, await _members(s, cid))


# ------------------------------------------------------------------ messages -> tasks


@router.get("/conversations/{cid}/messages")
async def list_messages(cid: int, before_id: int | None = None, limit: int = 100, _: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    await _conv(s, cid)
    q = select(Message).where(Message.conversation_id == cid)
    if before_id:
        q = q.where(Message.id < before_id)
    rows = (await s.scalars(q.order_by(Message.id.desc()).limit(min(limit, 500)))).all()
    return [message_dict(m) for m in reversed(rows)]


class Assignment(BaseModel):
    agent_id: int
    role: RoleLit


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    mode: Literal["manual", "auto", "chat"] = "manual"
    mentions: list[int] = []
    mention_all: bool = False
    assignments: list[Assignment] = []


@router.post("/conversations/{cid}/messages")
async def send_message(cid: int, body: MessageIn, request: Request, user: User = Depends(require(Role.operator)),
                       s: AsyncSession = Depends(get_session)):
    """Post a user message. In manual/auto mode it becomes a collaborative task; mode=chat only records it."""
    c = await _conv(s, cid)
    members = await _members(s, cid)
    member_ids = {a.id: (m, a) for m, a in members}
    parsed, parsed_all = parse_mentions(body.content, [(a.id, a.name) for _, a in members])
    mentioned = list(dict.fromkeys([*body.mentions, *parsed]))
    mention_all = body.mention_all or parsed_all
    unknown = [i for i in mentioned if i not in member_ids]
    if unknown:
        raise HTTPException(422, f"被 @ 的 Agent 不在群聊中: {unknown}")

    msg = Message(conversation_id=cid, sender_type="user", sender_id=user.id, sender_name=user.username, kind="text",
                  content=body.content, mentions=mentioned + (["all"] if mention_all else []))
    s.add(msg)
    c.updated_at = utcnow()
    if body.mode == "chat":
        await s.commit()
        bus.publish("message_created", message_dict(msg))
        return {"message": message_dict(msg), "task": None}

    # Who participates, and in which role
    if body.assignments:
        bad = [x.agent_id for x in body.assignments if x.agent_id not in member_ids]
        if bad:
            raise HTTPException(422, f"分工中的 Agent 不在群聊中: {bad}")
        assignments = [(x.agent_id, x.role) for x in body.assignments]
    else:
        targets = [a.id for _, a in members] if mention_all or (body.mode == "auto" and not mentioned) else mentioned
        assignments = [(aid, member_ids[aid][0].role) for aid in targets]
    if body.mode == "manual" and not assignments:
        raise HTTPException(422, "人工指定模式请 @ 至少一个 Agent、@全体，或在「分工」中指定；也可切换为自动协作")
    if not assignments:
        raise HTTPException(422, "群聊中没有可用的 Agent 成员")
    disabled = [member_ids[aid][1].name for aid, _ in assignments if not member_ids[aid][1].enabled]
    if disabled:
        raise HTTPException(422, f"以下 Agent 已停用: {', '.join(disabled)}")

    use_coordinator = body.mode == "auto" and c.coordinator_agent_id and not body.assignments
    root = Task(project_id=c.project_id, title=body.content.strip().splitlines()[0][:80], prompt=body.content,
                mode="auto" if body.mode == "auto" else "manual", status=TaskStatus.pending.value, conversation_id=cid,
                trace_id=new_trace_id(), created_by=user.id)
    s.add(root)
    await s.flush()
    root.workspace_dir = str(get_settings().workspaces_dir / f"task-{root.id}")
    msg.task_id = root.id

    if use_coordinator:
        coord = member_ids.get(c.coordinator_agent_id)
        if not coord or not coord[1].enabled:
            raise HTTPException(422, "主控 Agent 不可用（已移出群聊或已停用）")
        s.add(TaskStep(root_task_id=root.id, step_key="plan", kind="plan", title="主控拆分任务", instruction="分析任务并输出协作计划",
                       role="analyst", agent_id=c.coordinator_agent_id, origin="plan", seq=0))
        intro = f"任务 #{root.id} 已创建（自动协作）：由主控 {coord[1].name} 拆分并分配。"
    else:
        steps = manual_steps(assignments)
        for i, p in enumerate(steps):
            a = member_ids[p["agent_id"]][1]
            s.add(TaskStep(root_task_id=root.id, step_key=p["key"], kind="work", title=f"{p['title']} · {a.name}",
                           instruction=p["instruction"], role=p["role"], agent_id=a.id, depends_on=p["depends_on"],
                           origin="user", seq=10 + i, requires_approval=bool(risk_warnings(a.adapter_type, a.config))))
        flow = " → ".join(
            "、".join(member_ids[p["agent_id"]][1].name for p in steps if p["key"][:2] == k)
            for k in dict.fromkeys(p["key"][:2] for p in steps))
        prefix = "未设置主控 Agent，平台调度器按成员角色分工。" if body.mode == "auto" else ""
        intro = f"任务 #{root.id} 已创建（{'自动协作' if body.mode == 'auto' else '人工指定'}）：{prefix}执行顺序 {flow}"
    await audit(s, "collab.task_create", user=user, target_type="task", target_id=root.id, request=request,
                detail={"conversation_id": cid, "mode": body.mode, "assignments": assignments})
    bus.publish("message_created", message_dict(msg))
    await post_message(cid, intro, task_id=root.id)
    orchestrator.start(root.id)
    return {"message": message_dict(msg), "task": {"id": root.id, "status": root.status, "mode": root.mode}}


# ------------------------------------------------------------------ collaborative task state & control


async def _root(s: AsyncSession, tid: int) -> Task:
    t = await s.get(Task, tid)
    if not t or t.conversation_id is None or t.parent_task_id is not None:
        raise HTTPException(404, "协作任务不存在")
    return t


async def collab_detail(s: AsyncSession, t: Task) -> dict:
    names = await _names(s)
    steps = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == t.id).order_by(TaskStep.seq, TaskStep.id))).all()
    handoffs = (await s.scalars(select(Handoff).where(Handoff.root_task_id == t.id).order_by(Handoff.id))).all()
    approvals = (await s.scalars(select(Approval).where(Approval.root_task_id == t.id).order_by(Approval.id))).all()
    return {"id": t.id, "conversation_id": t.conversation_id, "title": t.title, "prompt": t.prompt, "mode": t.mode,
            "status": t.status, "error": t.error, "trace_id": t.trace_id, "workspace_dir": t.workspace_dir,
            "created_at": t.created_at.isoformat(), "started_at": t.started_at.isoformat() if t.started_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            "active": orchestrator.is_active(t.id), "paused": orchestrator.is_paused(t.id),
            "steps": [step_dict(x, names.get(x.agent_id, "")) for x in steps],
            "handoffs": [handoff_dict(h, names) for h in handoffs],
            "approvals": [approval_dict(a) for a in approvals]}


@router.get("/conversations/{cid}/tasks")
async def conversation_tasks(cid: int, limit: int = 20, _: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(Task).where(Task.conversation_id == cid, Task.parent_task_id.is_(None))
                            .order_by(Task.id.desc()).limit(limit))).all()
    return [await collab_detail(s, t) for t in rows]


@router.get("/collab/tasks/{tid}")
async def get_collab_task(tid: int, _: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    return await collab_detail(s, await _root(s, tid))


async def _control(tid: int, action: str, request: Request, user: User, s: AsyncSession, note: str = ""):
    t = await _root(s, tid)
    if TaskStatus(t.status) in TERMINAL_TASK and action != "intervene":
        raise HTTPException(409, f"任务已结束（{t.status}）")
    if action == "pause":
        orchestrator.pause(t.id)
        text = f"⏸ {user.username} 暂停了任务 #{t.id}：正在执行的 Agent 会完成当前步骤（外部 Agent 进程无法冻结），之后不再启动新步骤。"
    elif action == "resume":
        orchestrator.resume(t.id)
        text = f"▶ {user.username} 恢复了任务 #{t.id}"
    elif action == "cancel":
        orchestrator.cancel(t.id)
        text = f"⏹ {user.username} 终止了任务 #{t.id}，正在结束所有 Agent 进程"
    else:
        await post_message(t.conversation_id, note, sender_type="user", sender_id=user.id, sender_name=user.username,
                           kind="intervention", task_id=t.id)
        text = f"✋ {user.username} 对任务 #{t.id} 进行了人工介入，补充说明将注入后续步骤"
    await audit(s, f"collab.{action}", user=user, target_type="task", target_id=t.id, request=request,
                detail={"note": note[:500]} if note else {})
    await post_message(t.conversation_id, text, task_id=t.id)
    notify(t.id, t.conversation_id)
    return await collab_detail(s, t)


@router.post("/collab/tasks/{tid}/pause")
async def pause_task(tid: int, request: Request, user: User = Depends(require(Role.operator)), s: AsyncSession = Depends(get_session)):
    return await _control(tid, "pause", request, user, s)


@router.post("/collab/tasks/{tid}/resume")
async def resume_task(tid: int, request: Request, user: User = Depends(require(Role.operator)), s: AsyncSession = Depends(get_session)):
    return await _control(tid, "resume", request, user, s)


@router.post("/collab/tasks/{tid}/cancel")
async def cancel_collab(tid: int, request: Request, user: User = Depends(require(Role.operator)), s: AsyncSession = Depends(get_session)):
    return await _control(tid, "cancel", request, user, s)


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=10_000)


@router.post("/collab/tasks/{tid}/intervene")
async def intervene(tid: int, body: NoteIn, request: Request, user: User = Depends(require(Role.operator)),
                    s: AsyncSession = Depends(get_session)):
    return await _control(tid, "intervene", request, user, s, note=body.note)


# ------------------------------------------------------------------ step operations (human intervention)


async def _step(s: AsyncSession, sid: int) -> tuple[TaskStep, Task]:
    st = await s.get(TaskStep, sid)
    if not st:
        raise HTTPException(404, "步骤不存在")
    return st, await s.get(Task, st.root_task_id)


class ReassignIn(BaseModel):
    agent_id: int
    instruction: str | None = None


@router.post("/collab/steps/{sid}/reassign")
async def reassign_step(sid: int, body: ReassignIn, request: Request, user: User = Depends(require(Role.operator)),
                        s: AsyncSession = Depends(get_session)):
    st, root = await _step(s, sid)
    if st.status not in (StepStatus.pending.value, StepStatus.waiting_approval.value, StepStatus.failed.value, StepStatus.skipped.value,
                         StepStatus.cancelled.value):
        raise HTTPException(409, f"步骤状态为 {st.status}，不能重新分配")
    members = {a.id: a for _, a in await _members(s, root.conversation_id)}
    a = members.get(body.agent_id)
    if not a:
        raise HTTPException(422, "目标 Agent 不在群聊中")
    old = st.agent_id
    st.agent_id = a.id
    if body.instruction:
        st.instruction = body.instruction
    st.requires_approval = bool(risk_warnings(a.adapter_type, a.config))
    st.approved = False
    if st.status == StepStatus.waiting_approval.value:
        st.status = StepStatus.pending.value
        await s.execute(Approval.__table__.update().where(Approval.step_id == st.id, Approval.status == "pending")
                        .values(status="cancelled", comment="步骤已重新分配"))
    s.add(Handoff(root_task_id=root.id, from_step_id=st.id, to_step_id=st.id, from_agent_id=old, to_agent_id=a.id,
                  initiated_by="user", reason=f"{user.username} 重新分配", remaining_work=st.instruction, status="proposed"))
    await audit(s, "collab.step_reassign", user=user, target_type="step", target_id=st.id, request=request,
                detail={"from": old, "to": a.id})
    await post_message(root.conversation_id, f"🔀 {user.username} 将步骤「{st.title}」重新分配给 {a.name}", kind="handoff", task_id=root.id, step_id=st.id)
    await _restart_if_needed(s, root, st)
    return await collab_detail(s, root)


async def _restart_if_needed(s: AsyncSession, root: Task, st: TaskStep):
    """Re-open a failed/skipped step (and skipped dependents) and make sure the orchestrator is driving."""
    if st.status in (StepStatus.failed.value, StepStatus.skipped.value, StepStatus.cancelled.value):
        st.status, st.error, st.subtask_id, st.output_text = StepStatus.pending.value, None, None, None
        all_steps = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == root.id))).all()
        reopened = {st.step_key}
        changed = True
        while changed:
            changed = False
            for x in all_steps:
                if x.step_key not in reopened and x.status == StepStatus.skipped.value and set(x.depends_on) & reopened:
                    x.status, x.error = StepStatus.pending.value, None
                    reopened.add(x.step_key)
                    changed = True
    await s.commit()
    if TaskStatus(root.status) in TERMINAL_TASK or not orchestrator.is_active(root.id):
        orchestrator.start(root.id)
    else:
        orchestrator.poke(root.id)
    notify(root.id, root.conversation_id)


@router.post("/collab/steps/{sid}/retry")
async def retry_step(sid: int, request: Request, user: User = Depends(require(Role.operator)), s: AsyncSession = Depends(get_session)):
    st, root = await _step(s, sid)
    if st.status not in (StepStatus.failed.value, StepStatus.cancelled.value, StepStatus.skipped.value):
        raise HTTPException(409, "只有失败、取消或跳过的步骤可以重试")
    await audit(s, "collab.step_retry", user=user, target_type="step", target_id=st.id, request=request)
    await post_message(root.conversation_id, f"🔄 {user.username} 重试步骤「{st.title}」", task_id=root.id, step_id=st.id)
    await _restart_if_needed(s, root, st)
    return await collab_detail(s, root)


@router.post("/collab/steps/{sid}/skip")
async def skip_step(sid: int, request: Request, user: User = Depends(require(Role.operator)), s: AsyncSession = Depends(get_session)):
    st, root = await _step(s, sid)
    if st.status not in (StepStatus.pending.value, StepStatus.waiting_approval.value):
        raise HTTPException(409, "只能跳过尚未开始的步骤")
    st.status, st.error = StepStatus.skipped.value, f"{user.username} 手动跳过"
    await audit(s, "collab.step_skip", user=user, target_type="step", target_id=st.id, request=request)
    orchestrator.poke(root.id)
    notify(root.id, root.conversation_id)
    return await collab_detail(s, root)


class StepIn(BaseModel):
    agent_id: int
    title: str = Field(min_length=1, max_length=256)
    instruction: str = Field(min_length=1, max_length=20_000)
    role: RoleLit = "executor"
    depends_on: list[str] = []


@router.post("/collab/tasks/{tid}/steps")
async def add_step(tid: int, body: StepIn, request: Request, user: User = Depends(require(Role.operator)),
                   s: AsyncSession = Depends(get_session)):
    root = await _root(s, tid)
    members = {a.id: a for _, a in await _members(s, root.conversation_id)}
    a = members.get(body.agent_id)
    if not a:
        raise HTTPException(422, "Agent 不在群聊中")
    keys = set((await s.scalars(select(TaskStep.step_key).where(TaskStep.root_task_id == tid))).all())
    bad = [d for d in body.depends_on if d not in keys]
    if bad:
        raise HTTPException(422, f"依赖步骤不存在: {bad}")
    n = await s.scalar(select(func.count(TaskStep.id)).where(TaskStep.root_task_id == tid, TaskStep.origin == "intervention"))
    st = TaskStep(root_task_id=tid, step_key=f"u{n + 1}", kind="work", title=body.title, instruction=body.instruction, role=body.role,
                  agent_id=a.id, depends_on=body.depends_on, origin="intervention", seq=500 + n,
                  requires_approval=bool(risk_warnings(a.adapter_type, a.config)))
    s.add(st)
    await s.flush()
    if not body.depends_on:
        s.add(Handoff(root_task_id=tid, to_step_id=st.id, to_agent_id=a.id, initiated_by="user", reason=f"{user.username} 追加任务",
                      remaining_work=body.instruction, context=root.prompt[:2000]))
    await audit(s, "collab.step_add", user=user, target_type="task", target_id=tid, request=request,
                detail={"agent_id": a.id, "title": body.title})
    await post_message(root.conversation_id, f"➕ {user.username} 追加步骤「{body.title}」给 {a.name}", task_id=tid, step_id=st.id)
    await _restart_if_needed(s, root, st)
    return await collab_detail(s, root)


# ------------------------------------------------------------------ approvals


@router.get("/approvals")
async def list_approvals(status: str | None = "pending", _: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    q = select(Approval).order_by(Approval.id.desc()).limit(200)
    if status:
        q = q.where(Approval.status == status)
    return [approval_dict(a) for a in (await s.scalars(q)).all()]


class DecisionIn(BaseModel):
    comment: str = ""


async def _decide(aid: int, approve: bool, body: DecisionIn, request: Request, user: User, s: AsyncSession):
    a = await s.get(Approval, aid)
    if not a:
        raise HTTPException(404, "审批不存在")
    if a.status != "pending":
        raise HTTPException(409, f"审批已处理（{a.status}）")
    if a.kind == "high_risk_step" and user.role != Role.admin.value:
        raise HTTPException(403, "高风险步骤需要管理员审批")
    root = await s.get(Task, a.root_task_id)
    a.status, a.decided_by, a.decided_at, a.comment = ("approved" if approve else "rejected"), user.id, utcnow(), body.comment
    if a.kind == "plan":
        steps = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == root.id,
                                                        TaskStep.status == StepStatus.waiting_approval.value, TaskStep.origin == "plan"))).all()
        for st in steps:
            st.status = StepStatus.pending.value if approve else StepStatus.skipped.value
            if not approve:
                st.error = "计划被拒绝"
    elif a.step_id:
        st = await s.get(TaskStep, a.step_id)
        if approve:
            st.approved, st.status = True, StepStatus.pending.value
        else:
            st.status, st.error = StepStatus.skipped.value, f"审批被拒绝：{body.comment or '无说明'}"
    await audit(s, "approval.approve" if approve else "approval.reject", user=user, target_type="approval", target_id=a.id,
                request=request, detail={"kind": a.kind, "task_id": root.id, "comment": body.comment})
    await post_message(root.conversation_id, f"{'✅' if approve else '🚫'} {user.username} {'批准' if approve else '拒绝'}了「{a.title}」"
                       + (f"：{body.comment}" if body.comment else ""), kind="approval", task_id=root.id, data={"approval_id": a.id})
    if a.kind == "plan" and not approve:
        orchestrator.cancel(root.id)
    orchestrator.poke(root.id)
    notify(root.id, root.conversation_id)
    return approval_dict(a)


@router.post("/approvals/{aid}/approve")
async def approve(aid: int, body: DecisionIn, request: Request, user: User = Depends(require(Role.operator)),
                  s: AsyncSession = Depends(get_session)):
    return await _decide(aid, True, body, request, user, s)


@router.post("/approvals/{aid}/reject")
async def reject(aid: int, body: DecisionIn, request: Request, user: User = Depends(require(Role.operator)),
                 s: AsyncSession = Depends(get_session)):
    return await _decide(aid, False, body, request, user, s)


@router.get("/handoffs")
async def list_handoffs(root_task_id: int | None = None, limit: int = 100, _: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    q = select(Handoff).order_by(Handoff.id.desc()).limit(min(limit, 500))
    if root_task_id:
        q = q.where(Handoff.root_task_id == root_task_id)
    names = await _names(s)
    return [handoff_dict(h, names) for h in (await s.scalars(q)).all()]
