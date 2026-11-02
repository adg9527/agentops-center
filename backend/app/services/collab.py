"""Multi-agent collaboration: plans, step DAG scheduling, structured handoffs, approvals.

A collaborative request is a root Task (mode=manual|auto, conversation_id set). Its work is a set of
TaskSteps forming a DAG via `depends_on`. Each step runs as a subtask (Task.parent_task_id = root)
through the regular TaskRunner, sharing the root's trace_id and workspace, so every agent's events
chain into one trace and files written by one agent are visible to the next.

Handoffs are structured rows, never just chat text:
  * platform handoff — created when a dependency finishes, carrying its result to the downstream step;
  * agent handoff — an agent ends its reply with a ```handoff {json}``` block; the platform validates
    the target member and creates a new step + Handoff row.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from sqlalchemy import func, select, update

from ..api.agents import risk_warnings
from ..db import session_factory, utcnow
from ..models import (
    ROLE_STAGE, TERMINAL_STEP, Agent, AgentRun, Approval, Conversation, ConversationMember, Handoff, Message,
    MemberRole, RunStatus, StepStatus, Task, TaskStatus, TaskStep,
)
from .event_bus import bus
from .task_runner import runner
from .trace import new_span_id, record_event, task_span_id
from ..chains import planner as chain_planner
from ..chains import summarizer as chain_summarizer
from ..chains.callbacks import build_callbacks, usage_totals
from ..chains.llm import llm_available
from ..chains.plan import PlanError, validate_plan

log = logging.getLogger(__name__)

MAX_AGENT_HANDOFFS = 5
MAX_PLAN_STEPS = 12

ROLE_LABEL = {"analyst": "分析", "executor": "执行", "reviewer": "审查", "summarizer": "汇总"}
ROLE_INSTRUCTION = {
    "analyst": "分析该任务：澄清目标、拆解要点、指出风险并给出实施方案。不要直接实现。",
    "executor": "执行该任务，完成实际工作并说明你做了什么、产出在哪里。",
    "reviewer": "审查前序成员的结果：逐条指出问题与改进建议，最后给出结论（通过 / 需修改）。",
    "summarizer": "汇总所有成员的工作，形成一份面向用户的最终报告。",
}


# ------------------------------------------------------------------ pure helpers (unit-tested)


def parse_mentions(text: str, members: list[tuple[int, str]]) -> tuple[list[int], bool]:
    """Find @Name mentions (names may contain spaces) and @全体 / @all."""
    mention_all = bool(re.search(r"@(全体|所有人|all|everyone)\b", text, re.I)) or "@全体" in text
    found: list[int] = []
    for agent_id, name in sorted(members, key=lambda m: -len(m[1])):
        if f"@{name}" in text and agent_id not in found:
            found.append(agent_id)
    return found, mention_all


_FENCE = re.compile(r"```(?:json|handoff)?\s*(\{.*?\})\s*```", re.S)


def _first_json(text: str, label: str | None = None) -> dict | None:
    if label:
        m = re.search(r"```" + label + r"\s*(\{.*?\})\s*```", text, re.S)
        blocks = [m.group(1)] if m else []
    else:
        blocks = _FENCE.findall(text)
        if not blocks:
            s, e = text.find("{"), text.rfind("}")
            blocks = [text[s:e + 1]] if s != -1 and e > s else []
    for b in blocks:
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            continue
    return None


def parse_plan(text: str, members: dict[str, int]) -> list[dict]:
    """Validate a coordinator plan emitted as JSON text. Raises PlanError."""
    data = _first_json(text)
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        raise PlanError("主控输出中没有找到合法的 JSON 计划（需要 {\"steps\": [...]}）")
    return validate_plan(data["steps"], members, MAX_PLAN_STEPS)


def extract_handoff(text: str) -> dict | None:
    data = _first_json(text, "handoff")
    if not isinstance(data, dict) or not data.get("to") or not data.get("remaining"):
        return None
    return {
        "to": str(data["to"]),
        "reason": str(data.get("reason") or ""),
        "done": str(data.get("done") or ""),
        "remaining": str(data["remaining"]),
        "files": [str(f) for f in (data.get("files") or [])][:50],
    }


def manual_steps(assignments: list[tuple[int, str]]) -> list[dict]:
    """Group (agent_id, role) by stage; each stage depends on all steps of the previous stage."""
    stages: dict[int, list[tuple[int, str]]] = {}
    for agent_id, role in assignments:
        stages.setdefault(ROLE_STAGE.get(role, 2), []).append((agent_id, role))
    out, prev = [], []
    for n, stage in enumerate(sorted(stages)):
        cur = []
        for i, (agent_id, role) in enumerate(stages[stage]):
            key = f"s{n + 1}{chr(97 + i)}"
            out.append({"key": key, "agent_id": agent_id, "role": role, "title": f"{ROLE_LABEL[role]}",
                        "instruction": ROLE_INSTRUCTION[role], "depends_on": list(prev)})
            cur.append(key)
        prev = cur
    return out


def list_files(workspace: str, limit: int = 100) -> list[str]:
    root = Path(workspace)
    if not root.is_dir():
        return []
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith("_task_prompt"):
            files.append(p.relative_to(root).as_posix())
            if len(files) >= limit:
                break
    return files


def _clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n…（已截断，原文 {len(s)} 字）"


# ------------------------------------------------------------------ persistence helpers


def message_dict(m: Message) -> dict:
    return {"id": m.id, "conversation_id": m.conversation_id, "sender_type": m.sender_type, "sender_id": m.sender_id,
            "sender_name": m.sender_name, "kind": m.kind, "content": m.content, "mentions": m.mentions,
            "task_id": m.task_id, "step_id": m.step_id, "data": m.data,
            "created_at": m.created_at.isoformat() if m.created_at else None}


async def post_message(conversation_id: int, content: str, *, sender_type="system", sender_id=None, sender_name="系统",
                       kind="status", task_id=None, step_id=None, data=None, mentions=None) -> dict:
    async with session_factory()() as s:
        m = Message(conversation_id=conversation_id, sender_type=sender_type, sender_id=sender_id,
                    sender_name=sender_name, kind=kind, content=content, task_id=task_id, step_id=step_id,
                    data=data or {}, mentions=mentions or [])
        s.add(m)
        await s.commit()
        d = message_dict(m)
    bus.publish("message_created", d)
    return d


def notify(root_task_id: int, conversation_id: int | None):
    bus.publish("collab_updated", {"root_task_id": root_task_id, "conversation_id": conversation_id})


# ------------------------------------------------------------------ prompts


async def _members(s, conversation_id: int) -> list[tuple[ConversationMember, Agent]]:
    rows = await s.execute(
        select(ConversationMember, Agent).join(Agent, Agent.id == ConversationMember.agent_id)
        .where(ConversationMember.conversation_id == conversation_id, Agent.deleted.is_(False))
        .order_by(ConversationMember.id)
    )
    return list(rows.all())


async def _history(s, conversation_id: int, before_task: int, limit: int = 8) -> str:
    msgs = (await s.scalars(
        select(Message).where(Message.conversation_id == conversation_id, Message.kind.in_(["text", "result"]),
                              (Message.task_id.is_(None)) | (Message.task_id < before_task))
        .order_by(Message.id.desc()).limit(limit)
    )).all()
    return "\n".join(f"- {m.sender_name}: {_clip(m.content, 300)}" for m in reversed(msgs))


def plan_prompt(request: str, members: list[tuple[ConversationMember, Agent]], history: str) -> str:
    roster = "\n".join(
        f"- {a.name}｜角色: {ROLE_LABEL.get(m.role, m.role)}｜能力: {', '.join(a.capability_tags) or '未标注'}｜{a.description or ''}"
        for m, a in members
    )
    return f"""你是多 Agent 团队的主控（协调者）。请把用户任务拆分为子任务并分配给合适的成员。

## 用户任务
{request}

## 可分配成员（agent 字段必须严格使用以下名称之一）
{roster}

## 群聊近期记录
{history or '（无）'}

## 输出要求
只输出一个 ```json 代码块，不要输出其他内容，格式：
```json
{{"steps": [
  {{"id": "s1", "agent": "成员名", "role": "analyst|executor|reviewer|summarizer", "title": "简短标题",
    "instruction": "该成员需要完成的具体工作", "depends_on": []}}
]}}
```
规则：步骤数 1-{MAX_PLAN_STEPS}；depends_on 填写必须先完成的步骤 id，无依赖的步骤会并行执行；不要安排汇总步骤（平台会让你在最后汇总）。"""


def summary_prompt(request: str, results: list[tuple[str, str, str]]) -> str:
    body = "\n\n".join(f"### {title}（{agent}）\n{_clip(out, 4000)}" for title, agent, out in results)
    return f"""你是团队主控。所有子任务已结束，请基于各成员结果向用户给出最终汇总报告：结论、关键产出、遗留问题与建议。

## 用户任务
{request}

## 各成员结果
{body}"""


def step_prompt(*, request: str, step: TaskStep, agent: Agent, role: str, handoffs: list[tuple[Handoff, str]],
                interventions: list[str], history: str, teammates: list[str]) -> str:
    parts = [f"你是多 Agent 协作团队成员「{agent.name}」，本次角色：{ROLE_LABEL.get(role, role)}。",
             f"## 总任务\n{request}", f"## 你的子任务：{step.title}\n{step.instruction}"]
    if handoffs:
        hs = []
        for h, from_name in handoffs:
            hs.append(f"### 来自 {from_name} 的交接（{h.reason}）\n已完成：{_clip(h.done_work, 1500)}\n"
                      f"待完成：{_clip(h.remaining_work, 800)}\n相关文件：{', '.join(h.files) or '无'}\n"
                      f"结果：\n{_clip(h.result, 4000)}")
        parts.append("## 上游交接\n" + "\n\n".join(hs))
    if interventions:
        parts.append("## 用户补充说明（优先遵守）\n" + "\n".join(f"- {i}" for i in interventions))
    if history:
        parts.append(f"## 群聊近期记录\n{history}")
    parts.append(
        "## 协作约定\n- 所有成员共用当前工作目录，可以读取前序成员留下的文件。\n"
        f"- 如需把剩余工作交给其他成员，在回复末尾附加（成员可选：{', '.join(teammates)}）：\n"
        '```handoff\n{"to": "成员名", "reason": "交接原因", "done": "已完成的工作", "remaining": "需要对方完成的工作", "files": ["相关文件"]}\n```'
    )
    return "\n\n".join(parts)


# ------------------------------------------------------------------ orchestrator


class Orchestrator:
    def __init__(self):
        self._jobs: dict[int, asyncio.Task] = {}
        self._wake: dict[int, asyncio.Event] = {}
        self._paused: set[int] = set()
        self._cancelled: set[int] = set()
        self._running: dict[int, dict[int, asyncio.Task]] = {}

    def is_active(self, root_id: int) -> bool:
        return root_id in self._jobs

    def is_paused(self, root_id: int) -> bool:
        return root_id in self._paused

    def start(self, root_id: int):
        if root_id in self._jobs:
            self.poke(root_id)
            return
        self._wake[root_id] = asyncio.Event()
        self._cancelled.discard(root_id)
        job = asyncio.create_task(self._drive(root_id))
        self._jobs[root_id] = job

        def _done(_):
            self._jobs.pop(root_id, None)
            self._wake.pop(root_id, None)
            self._running.pop(root_id, None)
            self._paused.discard(root_id)

        job.add_done_callback(_done)

    def poke(self, root_id: int):
        ev = self._wake.get(root_id)
        if ev:
            ev.set()

    def pause(self, root_id: int):
        self._paused.add(root_id)
        self.poke(root_id)

    def resume(self, root_id: int):
        self._paused.discard(root_id)
        self.poke(root_id)

    def cancel(self, root_id: int):
        self._cancelled.add(root_id)
        self._paused.discard(root_id)
        self.poke(root_id)

    async def wait(self, root_id: int):
        job = self._jobs.get(root_id)
        if job:
            await asyncio.shield(job)

    async def shutdown(self):
        for rid in list(self._jobs):
            self.cancel(rid)
        if self._jobs:
            await asyncio.wait(list(self._jobs.values()), timeout=15)

    # -------------------------------------------------------------- main loop

    async def _set_root(self, root_id: int, **fields):
        async with session_factory()() as s:
            await s.execute(update(Task).where(Task.id == root_id).values(**fields))
            await s.commit()
            t = await s.get(Task, root_id)
        bus.publish("task_updated", {"id": root_id, "status": t.status, "error": t.error})
        notify(root_id, t.conversation_id)

    async def _drive(self, root_id: int):
        try:
            await self._loop(root_id)
        except Exception as e:
            log.exception("collab task %s crashed", root_id)
            await self._set_root(root_id, status=TaskStatus.failed.value, error=f"编排器内部错误: {e}", finished_at=utcnow())

    async def _loop(self, root_id: int):
        async with session_factory()() as s:
            root = await s.get(Task, root_id)
        conv_id = root.conversation_id
        Path(root.workspace_dir).mkdir(parents=True, exist_ok=True)
        if root.started_at is None:
            await self._set_root(root_id, status=TaskStatus.running.value, started_at=utcnow())
            await record_event(trace_id=root.trace_id, span_id=task_span_id(root), event_type="task.started",
                               task_id=root_id, name=root.title, input_summary=root.prompt,
                               attributes={"mode": root.mode, "conversation_id": conv_id})
        else:
            await self._set_root(root_id, status=TaskStatus.running.value, finished_at=None, error=None)
        running = self._running.setdefault(root_id, {})
        wake = self._wake[root_id]

        while True:
            cancelled = root_id in self._cancelled
            paused = root_id in self._paused
            async with session_factory()() as s:
                steps = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == root_id).order_by(TaskStep.seq, TaskStep.id))).all()
                by_key = {st.step_key: st for st in steps}
                to_launch: list[TaskStep] = []
                notices: list[tuple[str, int, dict]] = []  # posted after commit (SQLite allows one writer)
                for st in steps:
                    if st.status != StepStatus.pending.value:
                        continue
                    if cancelled:
                        st.status, st.error = StepStatus.cancelled.value, "任务已终止"
                        continue
                    deps = [by_key.get(k) for k in st.depends_on]
                    if any(d is None or d.status in (StepStatus.failed.value, StepStatus.cancelled.value, StepStatus.skipped.value) for d in deps):
                        st.status, st.error = StepStatus.skipped.value, "上游步骤未成功，已跳过"
                        continue
                    if all(d.status == StepStatus.completed.value for d in deps):
                        if st.requires_approval and not st.approved:
                            st.status = StepStatus.waiting_approval.value
                            notice = await self._request_step_approval(s, root, st)
                            if notice:
                                notices.append(notice)
                        elif not paused:
                            to_launch.append(st)
                if cancelled:
                    for st in steps:
                        if st.status in (StepStatus.waiting_approval.value, StepStatus.waiting_handoff.value):
                            st.status, st.error = StepStatus.cancelled.value, "任务已终止"
                    await s.execute(update(Approval).where(Approval.root_task_id == root_id, Approval.status == "pending")
                                    .values(status="cancelled", decided_at=utcnow()))
                await s.commit()
            for text, step_id, data in notices:
                await post_message(root.conversation_id, text, kind="approval", task_id=root_id, step_id=step_id, data=data)

            if cancelled:
                for st_id, job in running.items():
                    async with session_factory()() as s:
                        st = await s.get(TaskStep, st_id)
                    if st and st.subtask_id:
                        runner.cancel(st.subtask_id)
            for st in to_launch:
                running[st.id] = asyncio.create_task(self._run_step(root, st.id))

            async with session_factory()() as s:
                steps = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == root_id))).all()
            waiting = [st for st in steps if st.status in (StepStatus.waiting_approval.value, StepStatus.waiting_handoff.value)]
            pending = [st for st in steps if st.status == StepStatus.pending.value]

            if not running and not waiting and (not pending or cancelled):
                if not cancelled and await self._maybe_add_summary(root, steps):
                    continue
                break

            if running:
                status = TaskStatus.running
            elif paused and pending:
                status = TaskStatus.paused
            elif waiting:
                status = TaskStatus.waiting_approval if any(st.status == StepStatus.waiting_approval.value for st in waiting) else TaskStatus.waiting_handoff
            else:
                status = TaskStatus.running
            async with session_factory()() as s:
                cur = (await s.get(Task, root_id)).status
            if cur != status.value:
                await self._set_root(root_id, status=status.value)

            wake.clear()
            waiters = [*running.values(), asyncio.create_task(wake.wait())]
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            waiters[-1].cancel()
            for st_id, job in list(running.items()):
                if job.done():
                    running.pop(st_id)
                    if job.exception():
                        log.error("step %s crashed: %s", st_id, job.exception())

        await self._finish(root_id)

    async def _finish(self, root_id: int):
        async with session_factory()() as s:
            root = await s.get(Task, root_id)
            steps = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == root_id))).all()
        if root_id in self._cancelled:
            final, err = TaskStatus.cancelled, "用户终止"
        else:
            bad = [st for st in steps if st.status != StepStatus.completed.value]
            final = TaskStatus.completed if steps and not bad else TaskStatus.failed
            err = None if final == TaskStatus.completed else f"{len(bad)}/{len(steps)} 个步骤未成功"
        await self._set_root(root_id, status=final.value, finished_at=utcnow(), error=err)
        await record_event(trace_id=root.trace_id, span_id=task_span_id(root), event_type=f"task.{final.value}",
                           task_id=root_id, name=root.title, status="ok" if final == TaskStatus.completed else "error",
                           output_summary=err)
        text = {TaskStatus.completed: f"✅ 任务 #{root_id} 已完成", TaskStatus.failed: f"❌ 任务 #{root_id} 未全部成功：{err}",
                TaskStatus.cancelled: f"⏹ 任务 #{root_id} 已终止"}[final]
        await post_message(root.conversation_id, text, task_id=root_id, kind="status", data={"status": final.value})

    async def _maybe_add_summary(self, root: Task, steps: list[TaskStep]) -> bool:
        """Auto mode: once all work is done, the coordinator writes the final report (once)."""
        if root.mode != "auto" or any(st.kind == "summary" for st in steps):
            return False
        work = [st for st in steps if st.kind == "work"]
        if not work or not any(st.status == StepStatus.completed.value for st in work):
            return False
        async with session_factory()() as s:
            conv = await s.get(Conversation, root.conversation_id)
            if not conv.coordinator_agent_id:
                return False
            s.add(TaskStep(root_task_id=root.id, step_key="summary", kind="summary", title="主控汇总",
                           instruction="汇总所有成员结果，形成最终报告", role=MemberRole.summarizer.value,
                           agent_id=conv.coordinator_agent_id, depends_on=[st.step_key for st in work],
                           origin="plan", seq=999))
            await s.commit()
        return True

    async def _request_step_approval(self, s, root: Task, st: TaskStep) -> tuple[str, int, dict] | None:
        """Create the approval row in the caller's transaction; returns the chat notice to post after commit."""
        exists = await s.scalar(select(func.count(Approval.id)).where(Approval.step_id == st.id, Approval.status == "pending"))
        if exists:
            return None
        agent = await s.get(Agent, st.agent_id)
        a = Approval(root_task_id=root.id, step_id=st.id, kind="high_risk_step",
                     title=f"步骤「{st.title}」将由高风险配置的 {agent.name} 执行",
                     detail={"agent_id": agent.id, "agent": agent.name, "risk_warnings": risk_warnings(agent.adapter_type, agent.config),
                             "instruction": st.instruction})
        s.add(a)
        await s.flush()
        return f"⚠️ 需要审批：{a.title}（{'；'.join(a.detail['risk_warnings'])}）", st.id, {"approval_id": a.id}

    # -------------------------------------------------------------- step execution

    async def _run_step(self, root: Task, step_id: int):
        async with session_factory()() as s:
            st = await s.get(TaskStep, step_id)
            agent = await s.get(Agent, st.agent_id)
            members = await _members(s, root.conversation_id)
            member_role = next((m.role for m, a in members if a.id == agent.id), st.role)
            if st.kind in ("plan", "summary") and llm_available():
                await self._run_chain_step(root, st, agent, members)
                return
            if st.kind == "plan":
                prompt = plan_prompt(root.prompt, members, await _history(s, root.conversation_id, root.id))
            elif st.kind == "summary":
                done = (await s.execute(
                    select(TaskStep, Agent.name).join(Agent, Agent.id == TaskStep.agent_id)
                    .where(TaskStep.root_task_id == root.id, TaskStep.kind == "work").order_by(TaskStep.seq, TaskStep.id)
                )).all()
                prompt = summary_prompt(root.prompt, [(x.title, n, x.output_text or f"（{x.status}）{x.error or ''}") for x, n in done])
            else:
                hrows = (await s.execute(
                    select(Handoff, Agent.name).outerjoin(Agent, Agent.id == Handoff.from_agent_id)
                    .where(Handoff.to_step_id == st.id).order_by(Handoff.id)
                )).all()
                interventions = (await s.scalars(
                    select(Message.content).where(Message.task_id == root.id, Message.kind == "intervention").order_by(Message.id)
                )).all()
                prompt = step_prompt(request=root.prompt, step=st, agent=agent, role=st.role or member_role,
                                     handoffs=[(h, n or "用户") for h, n in hrows], interventions=list(interventions),
                                     history=await _history(s, root.conversation_id, root.id),
                                     teammates=[a.name for _, a in members if a.id != agent.id])
            if not agent.enabled or agent.deleted:
                st.status, st.error, st.finished_at = StepStatus.failed.value, f"Agent {agent.name} 已停用", utcnow()
                await s.commit()
                await post_message(root.conversation_id, f"步骤「{st.title}」无法执行：{agent.name} 已停用", task_id=root.id, step_id=st.id)
                notify(root.id, root.conversation_id)
                return
            sub = Task(project_id=root.project_id, title=f"[{root.id}·{st.step_key}] {st.title}", prompt=prompt, mode="step",
                       status=TaskStatus.pending.value, parent_task_id=root.id, conversation_id=root.conversation_id,
                       trace_id=root.trace_id, workspace_dir=root.workspace_dir, created_by=root.created_by)
            s.add(sub)
            await s.flush()
            s.add(AgentRun(task_id=sub.id, agent_id=agent.id, agent_config_version=agent.config_version,
                           status=RunStatus.pending.value, span_id=new_span_id()))
            st.subtask_id, st.status, st.started_at, st.error = sub.id, StepStatus.running.value, utcnow(), None
            await s.execute(update(Handoff).where(Handoff.to_step_id == st.id, Handoff.status == "proposed")
                            .values(status="accepted", accepted_at=utcnow()))
            await s.commit()
            sub_id = sub.id
        notify(root.id, root.conversation_id)
        runner.submit(sub_id)
        await runner.wait(sub_id)

        async with session_factory()() as s:
            st = await s.get(TaskStep, step_id)
            run = await s.scalar(select(AgentRun).where(AgentRun.task_id == sub_id).order_by(AgentRun.id.desc()))
            ok = run is not None and run.status == RunStatus.completed.value
            st.output_text = run.output_text if run else None
            st.error = None if ok else (run.error if run else "未执行")
            st.status = (StepStatus.completed if ok else StepStatus.cancelled if run and run.status == RunStatus.cancelled.value
                         else StepStatus.failed).value
            st.finished_at = utcnow()
            await s.execute(update(Handoff).where(Handoff.to_step_id == st.id, Handoff.status == "accepted")
                            .values(status="completed" if ok else "rejected", completed_at=utcnow()))
            await s.commit()

        await post_message(
            root.conversation_id,
            st.output_text if ok else f"步骤「{st.title}」{'已取消' if st.status == 'cancelled' else '失败'}：{st.error}",
            sender_type="agent", sender_id=agent.id, sender_name=agent.name,
            kind={"plan": "plan", "summary": "result"}.get(st.kind, "result") if ok else "status",
            task_id=root.id, step_id=st.id,
            data={"step_key": st.step_key, "role": st.role, "status": st.status, "cost_usd": run.cost_usd if run else None,
                  "duration_ms": run.duration_ms if run else None},
        )
        if ok and st.kind == "plan":
            await self._apply_plan(root, st)
        elif ok and st.kind == "work":
            await self._propagate(root, st)
            await self._agent_handoff(root, st, agent)
        notify(root.id, root.conversation_id)

    async def _run_chain_step(self, root: Task, st: TaskStep, agent: Agent,
                              members: list[tuple[ConversationMember, Agent]]) -> None:
        """Plan/summary step executed by an LCEL chain instead of a dispatched agent.

        The chain runs in-process (no subtask, no external CLI), but it still records an
        AgentRun row so token/cost accounting and the run history stay uniform.
        """
        callbacks = build_callbacks()
        started = utcnow()
        history = ""
        done: list[tuple[TaskStep, str]] = []
        async with session_factory()() as s:
            if st.kind == "plan":
                history = await _history(s, root.conversation_id, root.id)
            else:
                done = list((await s.execute(
                    select(TaskStep, Agent.name).join(Agent, Agent.id == TaskStep.agent_id)
                    .where(TaskStep.root_task_id == root.id, TaskStep.kind == "work")
                    .order_by(TaskStep.seq, TaskStep.id)
                )).all())

        text: str | None = None
        error: str | None = None
        try:
            if st.kind == "plan":
                plan = await chain_planner.make_plan(
                    request=root.prompt, members=members, history=history,
                    max_steps=MAX_PLAN_STEPS, callbacks=callbacks,
                )
                names = {a.id: a.name for _m, a in members}
                text = json.dumps({"steps": [
                    {"id": p["key"], "agent": names[p["agent_id"]], "role": p["role"], "title": p["title"],
                     "instruction": p["instruction"], "depends_on": p["depends_on"]} for p in plan
                ]}, ensure_ascii=False)
            else:
                text = await chain_summarizer.summarize_results(
                    request=root.prompt,
                    results=[(x.title, n, x.output_text or f"（{x.status}）{x.error or ''}") for x, n in done],
                    callbacks=callbacks,
                )
        except Exception as e:  # a failing chain fails this step, it must not kill the root task
            log.exception("chain step %s (%s) failed", st.id, st.kind)
            error = f"{type(e).__name__}: {e}"[:2000]

        finished = utcnow()
        usage = usage_totals(callbacks, events=callbacks[0])
        async with session_factory()() as s:
            row = await s.get(TaskStep, st.id)
            row.output_text = text
            row.error = error
            row.status = (StepStatus.completed.value if error is None else StepStatus.failed.value)
            row.finished_at = finished
            s.add(AgentRun(
                task_id=root.id, agent_id=agent.id, agent_config_version=agent.config_version,
                status=RunStatus.completed.value if error is None else RunStatus.failed.value,
                span_id=new_span_id(), output_text=text, error=error, model_used=usage.model,
                input_tokens=usage.input_tokens or None, output_tokens=usage.output_tokens or None,
                started_at=started, finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
            ))
            await s.commit()
            step = await s.get(TaskStep, st.id)

        await post_message(
            root.conversation_id,
            text if error is None else f"「{st.title}」生成失败：{error}",
            sender_type="agent" if error is None else "system",
            sender_id=agent.id if error is None else None,
            sender_name=agent.name if error is None else "系统",
            kind={"plan": "plan", "summary": "result"}.get(st.kind, "result") if error is None else "status",
            task_id=root.id, step_id=st.id,
            data={"step_key": st.step_key, "role": st.role, "status": step.status, "engine": "langchain"},
        )
        if error is None and st.kind == "plan":
            await self._apply_plan(root, step)
        notify(root.id, root.conversation_id)

    async def _apply_plan(self, root: Task, plan_step: TaskStep):
        async with session_factory()() as s:
            conv = await s.get(Conversation, root.conversation_id)
            members = await _members(s, root.conversation_id)
            name_map = {a.name: a.id for _, a in members}
            try:
                plan = parse_plan(plan_step.output_text or "", name_map)
            except PlanError as e:
                st = await s.get(TaskStep, plan_step.id)
                st.status, st.error = StepStatus.failed.value, f"计划无效：{e}"
                await s.commit()
                await post_message(root.conversation_id, f"主控计划无效，任务无法继续：{e}", task_id=root.id, step_id=plan_step.id)
                return
            agents = {a.id: a for _, a in members}
            approval = None
            if conv.require_plan_approval:
                approval = Approval(root_task_id=root.id, kind="plan", title=f"任务 #{root.id} 的协作计划（{len(plan)} 步）",
                                    detail={"steps": plan})
                s.add(approval)
                await s.flush()
            for i, p in enumerate(plan):
                a = agents[p["agent_id"]]
                s.add(TaskStep(root_task_id=root.id, step_key=p["key"], kind="work", title=p["title"], instruction=p["instruction"],
                               role=p["role"], agent_id=a.id, depends_on=[plan_step.step_key, *p["depends_on"]], origin="plan",
                               seq=10 + i, requires_approval=bool(risk_warnings(a.adapter_type, a.config)),
                               status=StepStatus.waiting_approval.value if approval else StepStatus.pending.value))
            await s.commit()
        lines = "\n".join(f"{i + 1}. [{p['key']}] {agents[p['agent_id']].name}（{ROLE_LABEL[p['role']]}）：{p['title']}"
                          + (f"  ← 依赖 {', '.join(p['depends_on'])}" if p["depends_on"] else "") for i, p in enumerate(plan))
        await post_message(root.conversation_id, f"📋 主控已拆分任务：\n{lines}" + ("\n\n⏸ 计划需要审批后执行。" if approval else ""),
                           kind="approval" if approval else "status", task_id=root.id,
                           data={"approval_id": approval.id} if approval else {})
        # Plan -> member assignment handoffs (structured record of who received what).
        await self._propagate(root, plan_step, reason_prefix="主控分配任务")

    async def _propagate(self, root: Task, done: TaskStep, reason_prefix: str = "上游步骤完成"):
        """Create platform handoffs from a finished step to each step that depends on it."""
        files = list_files(root.workspace_dir)
        async with session_factory()() as s:
            downstream = (await s.scalars(select(TaskStep).where(TaskStep.root_task_id == root.id))).all()
            for d in downstream:
                if done.step_key not in d.depends_on:
                    continue
                exists = await s.scalar(select(func.count(Handoff.id)).where(Handoff.from_step_id == done.id, Handoff.to_step_id == d.id))
                if exists:
                    continue
                s.add(Handoff(root_task_id=root.id, from_step_id=done.id, to_step_id=d.id, from_agent_id=done.agent_id,
                              to_agent_id=d.agent_id, initiated_by="platform",
                              reason=f"{reason_prefix}：{done.title} → {d.title}", context=_clip(root.prompt, 2000),
                              done_work=_clip(done.output_text, 2000) if done.kind != "plan" else f"已制定计划，分配子任务「{d.title}」",
                              remaining_work=d.instruction, files=files,
                              result=_clip(done.output_text, 8000) if done.kind != "plan" else None))
            await s.commit()

    async def _agent_handoff(self, root: Task, st: TaskStep, agent: Agent):
        req = extract_handoff(st.output_text or "")
        if not req:
            return
        async with session_factory()() as s:
            members = await _members(s, root.conversation_id)
            target = next(((m, a) for m, a in members if a.name == req["to"] or a.name.lower() == req["to"].lower()), None)
            count = await s.scalar(select(func.count(Handoff.id)).where(Handoff.root_task_id == root.id, Handoff.initiated_by == "agent"))
            if target is None:
                await post_message(root.conversation_id, f"{agent.name} 请求交接给「{req['to']}」，但该成员不在群聊中，交接未执行。",
                                   kind="handoff", task_id=root.id, step_id=st.id)
                return
            if count >= MAX_AGENT_HANDOFFS:
                await post_message(root.conversation_id, f"Agent 主动交接次数已达上限（{MAX_AGENT_HANDOFFS}），忽略 {agent.name} 的交接请求。",
                                   kind="handoff", task_id=root.id, step_id=st.id)
                return
            m, a = target
            new = TaskStep(root_task_id=root.id, step_key=f"h{count + 1}", kind="work", title=f"接手：{_clip(req['remaining'], 40)}",
                           instruction=req["remaining"], role=m.role, agent_id=a.id, depends_on=[st.step_key], origin="handoff",
                           seq=st.seq + 1, requires_approval=bool(risk_warnings(a.adapter_type, a.config)))
            s.add(new)
            await s.flush()
            h = Handoff(root_task_id=root.id, from_step_id=st.id, to_step_id=new.id, from_agent_id=agent.id, to_agent_id=a.id,
                        initiated_by="agent", reason=req["reason"] or "Agent 主动交接", context=_clip(root.prompt, 2000),
                        done_work=req["done"], remaining_work=req["remaining"], files=req["files"] or list_files(root.workspace_dir),
                        result=_clip(st.output_text, 8000))
            s.add(h)
            await s.commit()
        await post_message(root.conversation_id, f"🔁 {agent.name} 将任务交接给 {a.name}：{req['reason'] or req['remaining'][:80]}",
                           kind="handoff", task_id=root.id, step_id=st.id, data={"to_agent_id": a.id})
        await record_event(trace_id=root.trace_id, span_id=new_span_id(), parent_span_id=task_span_id(root), event_type="handoff.created",
                           task_id=root.id, agent_id=agent.id, name=f"{agent.name} → {a.name}",
                           input_summary=req["remaining"], output_summary=req["done"],
                           attributes={"from_agent_id": agent.id, "to_agent_id": a.id, "reason": req["reason"], "files": req["files"]})


orchestrator = Orchestrator()


async def recover_collab():
    """Steps/approvals of tasks interrupted by a restart can't resume; mark them so the UI is truthful."""
    async with session_factory()() as s:
        live = [StepStatus.pending.value, StepStatus.running.value, StepStatus.waiting_approval.value, StepStatus.waiting_handoff.value]
        await s.execute(update(TaskStep).where(TaskStep.status.in_(live)).values(status=StepStatus.failed.value, error="服务重启，步骤被中断"))
        await s.execute(update(Approval).where(Approval.status == "pending").values(status="cancelled", comment="服务重启"))
        await s.commit()


__all__ = ["orchestrator", "recover_collab", "parse_plan", "parse_mentions", "extract_handoff", "manual_steps",
           "post_message", "message_dict", "notify", "PlanError", "TERMINAL_STEP"]
