"""LangGraph orchestration for a single task.

The graph is the *task workflow*; the actual work travels through the broker:

```text
START → plan ─┬─ Send(execute, job#1) ─┐
              ├─ Send(execute, job#2) ─┼→ finalize → END
              └─ Send(execute, job#N) ─┘
```

* `plan`    — 读取任务与运行记录，把任务置为 running，产出每个 Agent 的作业
* `execute` — 把作业发布到 `agentops.tasks`，然后等待该 Agent 执行完（真正的执行由
              队列消费者完成，`TaskRunner.execute_job`），并把进度写到 `agentops.progress`
* `finalize`— 汇总所有 Agent 的结果，落任务状态、写 trace、给产物打快照

图的职责是“编排 + 状态”，队列的职责是“把作业交给执行者”，两者解耦：同一个任务
可以由多个 worker 消费，也可以在没有 RabbitMQ 时退回进程内队列。
"""

from __future__ import annotations

import asyncio
import logging
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .broker import PROGRESS_QUEUE, TASK_QUEUE, broker
from .event_bus import bus

log = logging.getLogger(__name__)


class TaskState(TypedDict, total=False):
    task_id: int
    cancel: asyncio.Event
    jobs: list[dict[str, Any]]
    statuses: Annotated[list[str], operator.add]
    final: str
    error: str


def build_task_graph(runner: Any):
    """Build the graph; `runner` owns the DB/agent execution details."""

    async def plan(state: TaskState) -> dict:
        prepared = await runner.prepare(state["task_id"])
        if not prepared["jobs"]:
            return {"jobs": [], "final": prepared.get("final", "failed")}
        return {"jobs": prepared["jobs"]}

    def fan_out(state: TaskState) -> list[Send] | str:
        if not state.get("jobs"):
            return "finalize"
        return [Send("execute", {**state, "job": job}) for job in state["jobs"]]

    async def execute(state: TaskState) -> dict:
        job = state["job"]
        # 作业进队列；真正的执行在 broker 消费者里（TaskRunner.execute_job）
        await broker.publish(TASK_QUEUE, job)
        status = await runner.wait_run(job["run_id"], state.get("cancel"))
        return {"statuses": [status]}

    async def finalize(state: TaskState) -> dict:
        final = state.get("final") or await runner.finalize(state["task_id"], state.get("statuses") or [])
        return {"final": final}

    graph = StateGraph(TaskState)
    graph.add_node("plan", plan)
    graph.add_node("execute", execute)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", fan_out, ["execute", "finalize"])
    graph.add_edge("execute", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


async def run_task(runner: Any, task_id: int, cancel: asyncio.Event) -> None:
    """Execute one task through the graph (called by `TaskRunner.submit`)."""
    # 队列消费者就是 runner.execute_job；重复注册无害（Broker 内部按队列去重）
    broker.on(TASK_QUEUE, runner.execute_job)

    async def _progress_to_bus(payload: dict) -> None:
        # 进度消息转成平台事件，顺便把 agentops.progress 排空（外部订阅者可自行接入）
        bus.publish("agent_progress", payload)

    broker.on(PROGRESS_QUEUE, _progress_to_bus)
    app = build_task_graph(runner)
    try:
        await app.ainvoke({"task_id": task_id, "cancel": cancel, "statuses": []},
                          config={"recursion_limit": 50})
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 图出错也要把任务收尾成 failed
        log.exception("workflow failed for task %s", task_id)
        await runner.fail_task(task_id, f"工作流异常: {type(exc).__name__}: {exc}")
