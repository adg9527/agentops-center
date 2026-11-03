"""Schedules task execution against external agents.

Concurrency: a global semaphore (max_global_concurrency) plus a per-agent semaphore (agent.max_concurrency).
Each attempt is its own AgentRun row, so retries stay auditable. All state lives in the DB, so on startup
`recover_interrupted` marks work orphaned by a restart as failed instead of leaving it "running" forever.
"""

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import select, update

from ..adapters import get_adapter
from ..chains.runnable import run_adapter
from ..adapters.base import AdapterEvent, RunRequest, RunResult
from ..config import get_settings
from ..db import session_factory, utcnow
from ..models import TERMINAL_TASK, Agent, AgentRun, RunStatus, Task, TaskStatus
from ..security import decrypt_secrets, redact
from .broker import broker
from .event_bus import bus
from .trace import new_span_id, record_event, task_span_id

log = logging.getLogger(__name__)


def run_to_dict(r: AgentRun) -> dict:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "agent_id": r.agent_id,
        "agent_config_version": r.agent_config_version,
        "attempt": r.attempt,
        "status": r.status,
        "span_id": r.span_id,
        "external_session_id": r.external_session_id,
        "output_text": r.output_text,
        "error": r.error,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": r.cost_usd,
        "model_used": r.model_used,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "duration_ms": r.duration_ms,
    }


class TaskRunner:
    def __init__(self):
        self._global: asyncio.Semaphore | None = None
        self._agent_sems: dict[tuple[int, int], asyncio.Semaphore] = {}
        self._cancels: dict[int, asyncio.Event] = {}
        self._jobs: dict[int, asyncio.Task] = {}

    def _gsem(self) -> asyncio.Semaphore:
        if self._global is None:
            self._global = asyncio.Semaphore(get_settings().max_global_concurrency)
        return self._global

    def _asem(self, agent: Agent) -> asyncio.Semaphore:
        key = (agent.id, max(1, agent.max_concurrency))
        if key not in self._agent_sems:
            self._agent_sems[key] = asyncio.Semaphore(key[1])
        return self._agent_sems[key]

    def submit(self, task_id: int):
        cancel = asyncio.Event()
        self._cancels[task_id] = cancel
        if get_settings().scheduler == "langgraph":
            from .workflow import run_task

            job = asyncio.create_task(run_task(self, task_id, cancel))
        else:
            job = asyncio.create_task(self._execute(task_id, cancel))
        self._jobs[task_id] = job
        job.add_done_callback(lambda _: (self._jobs.pop(task_id, None), self._cancels.pop(task_id, None)))

    def is_active(self, task_id: int) -> bool:
        return task_id in self._jobs

    def cancel(self, task_id: int) -> bool:
        ev = self._cancels.get(task_id)
        if ev:
            ev.set()
            return True
        return False

    async def wait(self, task_id: int):
        job = self._jobs.get(task_id)
        if job:
            await asyncio.shield(job)

    async def shutdown(self):
        for ev in self._cancels.values():
            ev.set()
        if self._jobs:
            await asyncio.wait(list(self._jobs.values()), timeout=15)

    async def _set_task(self, task_id: int, **fields):
        async with session_factory()() as s:
            await s.execute(update(Task).where(Task.id == task_id).values(**fields))
            await s.commit()
            task = await s.get(Task, task_id)
        bus.publish("task_updated", {"id": task_id, "status": task.status, "error": task.error})

    # ------------------------------------------------------- LangGraph workflow nodes

    async def prepare(self, task_id: int) -> dict:
        """`plan` 节点：把任务置为 running，并为每个 Agent 产出一个待投递的作业。"""
        async with session_factory()() as s:
            task = await s.get(Task, task_id)
            runs = list((await s.scalars(select(AgentRun).where(AgentRun.task_id == task_id))).all())
        if task is None:
            return {"jobs": [], "final": TaskStatus.failed.value}
        Path(task.workspace_dir).mkdir(parents=True, exist_ok=True)
        if task.started_at is None:
            await self._set_task(task_id, status=TaskStatus.running.value, started_at=utcnow())
            await record_event(trace_id=task.trace_id, span_id=task_span_id(task),
                               parent_span_id=await self._parent_span(task), event_type="task.started",
                               task_id=task_id, name=task.title, input_summary=task.prompt)
        else:
            await self._set_task(task_id, status=TaskStatus.running.value, finished_at=None, error=None)
        jobs = [{"task_id": task_id, "run_id": run.id, "agent_id": run.agent_id} for run in runs]
        if jobs:
            bus.publish("task_queued", {"id": task_id, "jobs": len(jobs)})
        return {"jobs": jobs}

    async def _parent_span(self, task: Task) -> str | None:
        if not task.parent_task_id:
            return None
        async with session_factory()() as s:
            parent = await s.get(Task, task.parent_task_id)
        return task_span_id(parent) if parent else None

    async def execute_job(self, payload: dict) -> None:
        """队列消费者：真正执行一个 Agent 作业，并把进度发到 `agentops.progress`。"""
        task_id, run_id = int(payload["task_id"]), int(payload["run_id"])
        async with session_factory()() as s:
            task = await s.get(Task, task_id)
            run = await s.get(AgentRun, run_id)
        if task is None or run is None:
            return
        cancel = self._cancels.setdefault(task_id, asyncio.Event())
        await broker.publish_progress({"task_id": task_id, "run_id": run_id, "agent_id": run.agent_id,
                                       "status": "started"})
        try:
            await self._run_agent(task, run, cancel)
        finally:
            async with session_factory()() as s:
                updated = await s.get(AgentRun, run_id)
            await broker.publish_progress({"task_id": task_id, "run_id": run_id, "agent_id": run.agent_id,
                                           "status": updated.status if updated else "unknown"})

    async def wait_run(self, run_id: int, cancel: asyncio.Event | None = None) -> str:
        """`execute` 节点在投递后等待该作业结束（真正的执行在消费者里）。

        取消时不要立刻返回：消费者还要把进程杀掉、把 run 标成 cancelled，
        否则任务会先变 cancelled 而 run 仍是 running。
        """
        cancelled_at: float | None = None
        while True:
            async with session_factory()() as s:
                run = await s.get(AgentRun, run_id)
            if run is None:
                return RunStatus.failed.value
            if run.status not in (RunStatus.pending.value, RunStatus.running.value):
                return run.status
            if cancel is not None and cancel.is_set():
                if cancelled_at is None:
                    cancelled_at = time.monotonic()
                elif time.monotonic() - cancelled_at > 15:
                    return RunStatus.cancelled.value
            await asyncio.sleep(0.2)

    async def finalize(self, task_id: int, statuses: list[str]) -> str:
        async with session_factory()() as s:
            task = await s.get(Task, task_id)
        if task is None:
            return TaskStatus.failed.value
        cancel = self._cancels.get(task_id) or asyncio.Event()
        return await self._finalize_task(task, list(statuses), cancel)

    async def fail_task(self, task_id: int, error: str) -> None:
        await self._set_task(task_id, status=TaskStatus.failed.value, finished_at=utcnow(), error=error[:2000])

    async def _finalize_task(self, task: Task, statuses: list[str], cancel: asyncio.Event) -> str:
        if cancel.is_set():
            final = TaskStatus.cancelled
        elif statuses and all(st == RunStatus.completed.value for st in statuses):
            final = TaskStatus.completed
        else:
            final = TaskStatus.failed
        err = None if final == TaskStatus.completed else f"{sum(st != 'completed' for st in statuses)}/{len(statuses)} 个 Agent 未成功"
        await self._set_task(task.id, status=final.value, finished_at=utcnow(), error=err)
        await record_event(trace_id=task.trace_id, span_id=task_span_id(task),
                           parent_span_id=await self._parent_span(task), event_type=f"task.{final.value}",
                           task_id=task.id, name=task.title,
                           status="ok" if final == TaskStatus.completed else "error", output_summary=err)
        try:
            from .artifacts import snapshot_task

            await snapshot_task(task.id, note=f"task {final.value}")
        except Exception:
            log.exception("artifact snapshot failed for task %s", task.id)
        return final.value

    async def _execute(self, task_id: int, cancel: asyncio.Event):
        """legacy 路径（AGENTOPS_SCHEDULER=legacy）：进程内并发执行，不走队列。"""
        prepared = await self.prepare(task_id)
        jobs = prepared.get("jobs") or []
        if not jobs:
            await self.fail_task(task_id, "没有可执行的 Agent 运行记录")
            return

        async def one(job: dict) -> str:
            async with session_factory()() as s:
                task = await s.get(Task, job["task_id"])
                run = await s.get(AgentRun, job["run_id"])
            if task is None or run is None:
                return RunStatus.failed.value
            return await self._run_agent(task, run, cancel)

        try:
            statuses = list(await asyncio.gather(*(one(j) for j in jobs)))
        except Exception as e:  # defensive: never leave a task stuck in running
            log.exception("task %s crashed", task_id)
            statuses = [RunStatus.failed.value]
            await self._set_task(task_id, error=f"内部错误: {e}")
        async with session_factory()() as s:
            task = await s.get(Task, task_id)
        if task is not None:
            await self._finalize_task(task, statuses, cancel)

    async def _run_agent(self, task: Task, first_run: AgentRun, cancel: asyncio.Event) -> str:
        run = first_run
        while True:
            status = await self._attempt(task, run, cancel)
            if status in (RunStatus.completed.value, RunStatus.cancelled.value) or cancel.is_set():
                return status
            async with session_factory()() as s:
                agent = await s.get(Agent, run.agent_id)
                if agent is None or run.attempt > agent.max_retries:
                    return status
                nxt = AgentRun(task_id=task.id, agent_id=agent.id, agent_config_version=agent.config_version,
                               attempt=run.attempt + 1, status=RunStatus.pending.value, span_id=new_span_id())
                s.add(nxt)
                await s.commit()
                run = nxt
            await record_event(trace_id=task.trace_id, span_id=run.span_id, parent_span_id=task_span_id(task),
                               event_type="agent.run.retry", task_id=task.id, run_id=run.id, agent_id=run.agent_id,
                               name=f"第 {run.attempt} 次尝试")

    async def _finish_run(self, run_id: int, **fields):
        async with session_factory()() as s:
            await s.execute(update(AgentRun).where(AgentRun.id == run_id).values(**fields))
            await s.commit()
            run = await s.get(AgentRun, run_id)
        bus.publish("run_updated", run_to_dict(run))

    async def _attempt(self, task: Task, run: AgentRun, cancel: asyncio.Event) -> str:
        async with session_factory()() as s:
            agent = await s.get(Agent, run.agent_id)
        trace_id, root = task.trace_id, task_span_id(task)
        common = dict(trace_id=trace_id, task_id=task.id, run_id=run.id, agent_id=agent.id)

        if not agent.enabled:
            await self._finish_run(run.id, status=RunStatus.failed.value, error="Agent 已停用，拒绝执行", finished_at=utcnow())
            await record_event(span_id=run.span_id, parent_span_id=root, event_type="agent.run.failed",
                               name=agent.name, status="error", output_summary="Agent 已停用", **common)
            return RunStatus.failed.value

        adapter = get_adapter(agent.adapter_type)
        secrets = decrypt_secrets(agent.secret_config)
        known = list(secrets.values())
        tool_spans: dict[str, str] = {}

        async def emit(ev: AdapterEvent):
            span, parent = run.span_id, root
            if ev.kind in ("tool.call", "tool.result") and ev.call_id:
                span = tool_spans.setdefault(ev.call_id, new_span_id())
                parent = run.span_id
            await record_event(span_id=span, parent_span_id=parent, event_type=ev.kind, name=ev.name, status=ev.status,
                               duration_ms=ev.duration_ms, input_summary=ev.input, output_summary=ev.output,
                               attributes=ev.attributes, known_secrets=known, **common)
            if ev.kind == "session.started":
                await self._update_detected(agent.id, ev.attributes)
            elif ev.kind in ("agent.message", "agent.thinking") and ev.output and task.mode != "plan":
                # 计划步骤输出的是 JSON 计划，不该当成聊天内容流式展示
                # 逐条文本推给前端，聊天窗口边跑边显示（最终仍以落库消息为准）
                bus.publish("agent_delta", {"task_id": task.id, "root_task_id": task.parent_task_id or task.id,
                                            "conversation_id": task.conversation_id,
                                            "agent_id": agent.id, "agent_name": agent.name, "text": ev.output,
                                            "thinking": ev.kind == "agent.thinking"})

        waited = False
        async with self._gsem(), self._asem(agent):
            if cancel.is_set():
                waited = True
            else:
                started = utcnow()
                await self._finish_run(run.id, status=RunStatus.running.value, started_at=started)
                await record_event(span_id=run.span_id, parent_span_id=root, event_type="agent.run.started",
                                   name=agent.name, input_summary=task.prompt, known_secrets=known,
                                   attributes={"adapter": agent.adapter_type, "attempt": run.attempt,
                                               "config_version": agent.config_version}, **common)
                req = RunRequest(prompt=task.prompt, workdir=Path(task.workspace_dir), config=agent.config,
                                 secrets=secrets, model=agent.model, timeout_sec=agent.timeout_sec)
                try:
                    result = await run_adapter(adapter, req, emit, cancel, task_id=task.id)
                except Exception as e:
                    log.exception("adapter %s crashed", agent.adapter_type)
                    result = RunResult(ok=False, error=f"Adapter 异常: {type(e).__name__}: {e}")
        if waited:
            await self._finish_run(run.id, status=RunStatus.cancelled.value, error="排队期间被取消", finished_at=utcnow())
            return RunStatus.cancelled.value

        finished = utcnow()
        if result.ok:
            status = RunStatus.completed
        elif result.cancelled:
            status = RunStatus.cancelled
        elif result.timed_out:
            status = RunStatus.timeout
        else:
            status = RunStatus.failed

        await self._finish_run(
            run.id,
            status=status.value,
            output_text=redact(result.output, known, limit=200_000),
            error=redact(result.error, known),
            external_session_id=result.session_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            model_used=result.model or agent.model or None,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
        )
        await record_event(span_id=run.span_id, parent_span_id=root,
                           event_type="agent.run.completed" if result.ok else f"agent.run.{status.value}",
                           name=agent.name, status="ok" if result.ok else "error",
                           duration_ms=int((finished - started).total_seconds() * 1000),
                           output_summary=result.output if result.ok else result.error, known_secrets=known,
                           attributes={"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                                       "cost_usd": result.cost_usd, "session_id": result.session_id}, **common)
        async with session_factory()() as s:
            a = await s.get(Agent, agent.id)
            if result.ok:
                a.health, a.last_seen_at = "online", finished
            await s.commit()
        return status.value

    async def _update_detected(self, agent_id: int, attrs: dict):
        async with session_factory()() as s:
            a = await s.get(Agent, agent_id)
            if a is None:
                return
            det = dict(a.detected or {})
            det["runtime"] = {k: v for k, v in attrs.items() if v is not None}
            det["runtime_detected_at"] = utcnow().isoformat()
            a.detected = det
            await s.commit()


async def recover_interrupted():
    """Mark tasks/runs left non-terminal by a previous process as failed (they can't still be running)."""
    async with session_factory()() as s:
        msg = "服务重启，任务被中断"
        live = [st.value for st in TaskStatus if st not in TERMINAL_TASK]
        res = await s.execute(
            update(Task).where(Task.status.in_(live)).values(status=TaskStatus.failed.value, error=msg, finished_at=utcnow())
        )
        await s.execute(
            update(AgentRun)
            .where(AgentRun.status.in_([RunStatus.pending.value, RunStatus.running.value]))
            .values(status=RunStatus.failed.value, error=msg, finished_at=utcnow())
        )
        await s.commit()
        if res.rowcount:
            log.warning("recovered %s interrupted task(s)", res.rowcount)


runner = TaskRunner()
