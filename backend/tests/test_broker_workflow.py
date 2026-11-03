"""Broker + LangGraph workflow: jobs really travel through the queue."""

import asyncio

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AgentAdapter, ConnectionResult, Level, RunResult
from app.services import broker as broker_module
from app.services.broker import TASK_QUEUE, broker


class QuickAdapter(AgentAdapter):
    type = "test_queue_agent"
    display_name = "Queue Agent"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = []
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        return RunResult(ok=True, output="通过队列执行完成", model="test")


@pytest.fixture(autouse=True)
def _register():
    ADAPTERS["test_queue_agent"] = QuickAdapter()
    yield
    ADAPTERS.pop("test_queue_agent", None)


async def test_broker_publish_and_consume():
    seen: list[dict] = []

    async def handler(payload: dict) -> None:
        seen.append(payload)

    broker.on(TASK_QUEUE, handler)
    await broker.publish(TASK_QUEUE, {"task_id": 1, "run_id": 2, "agent_id": 3})
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.05)

    assert seen and seen[0]["run_id"] == 2
    status = broker.status()
    assert status["backend"] in {"memory", "rabbitmq"}
    assert status["published"] >= 1 and status["consumed"] >= 1


async def test_task_jobs_are_published_to_the_queue(admin, client, monkeypatch):
    """任务执行必须经过队列：能看到 agentops.tasks 上的作业消息。"""
    published: list[tuple[str, dict]] = []
    real_publish = broker.publish

    async def spy(queue: str, payload: dict) -> None:
        published.append((queue, payload))
        await real_publish(queue, payload)

    monkeypatch.setattr(broker, "publish", spy)

    agent = (await client.post("/api/agents", json={"name": "队列Agent", "adapter_type": "test_queue_agent",
                                                    "model": ""})).json()
    task = (await client.post("/api/tasks", json={"prompt": "跑一下", "agent_ids": [agent["id"]]})).json()

    for _ in range(200):
        detail = (await client.get(f"/api/tasks/{task['id']}")).json()
        if detail["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)

    assert detail["status"] == "completed"
    jobs = [p for q, p in published if q == TASK_QUEUE]
    assert jobs and jobs[0]["task_id"] == task["id"] and jobs[0]["run_id"]


async def test_queue_status_endpoint(admin, client):
    data = (await client.get("/api/queue")).json()
    assert data["scheduler"] in {"langgraph", "legacy"}
    assert "queues" in data and "published" in data
