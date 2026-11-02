"""Relationship graph and mind-map endpoints."""

import asyncio

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AgentAdapter, ConnectionResult, Level, RunResult
from app.db import session_factory
from app.models import AgentRun, Task, TaskStep, TraceEvent, utcnow
from app.services.trace import new_span_id, new_trace_id


class QuickAdapter(AgentAdapter):
    type = "test_quick_graph"
    display_name = "Quick"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = []
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        return RunResult(ok=True, output="ok", model="test")


@pytest.fixture(autouse=True)
def _register():
    ADAPTERS["test_quick_graph"] = QuickAdapter()
    yield
    ADAPTERS.pop("test_quick_graph", None)


async def _agent(client, name="小图"):
    r = await client.post("/api/agents", json={"name": name, "adapter_type": "test_quick_graph", "model": ""})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _wait(client, task_id, timeout=30):
    for _ in range(timeout * 10):
        t = (await client.get(f"/api/tasks/{task_id}")).json()
        if t["status"] in ("completed", "failed", "cancelled") and not t["active"]:
            return t
        await asyncio.sleep(0.1)
    raise AssertionError("task did not finish")


async def test_plain_task_graph(admin, client):
    agent_id = await _agent(client)
    task = (await client.post("/api/tasks", json={"prompt": "跑一下", "agent_ids": [agent_id]})).json()
    await _wait(client, task["id"])

    r = await client.get(f"/api/graph/task/{task['id']}")
    assert r.status_code == 200
    data = r.json()
    ids = {n["id"] for n in data["nodes"]}
    assert f"task:{task['id']}" in ids
    assert f"agent:{agent_id}" in ids
    assert any(e["kind"] == "assign" for e in data["edges"])
    assert data["stats"]["total"] == 1 and data["stats"]["done"] == 1


async def test_collab_graph_has_steps_and_dependencies(admin, client):
    agent_id = await _agent(client)
    task = (await client.post("/api/tasks", json={"prompt": "协作", "agent_ids": [agent_id]})).json()
    await _wait(client, task["id"])

    async with session_factory()() as s:
        t = await s.get(Task, task["id"])
        t.mode = "auto"
        s.add_all([
            TaskStep(root_task_id=t.id, step_key="s1", kind="work", title="分析", instruction="a", role="analyst",
                     agent_id=agent_id, status="completed", seq=1),
            TaskStep(root_task_id=t.id, step_key="s2", kind="work", title="执行", instruction="b", role="executor",
                     agent_id=agent_id, status="running", depends_on=["s1"], seq=2),
        ])
        await s.commit()

    data = (await client.get(f"/api/graph/task/{task['id']}")).json()
    ids = {n["id"] for n in data["nodes"]}
    assert {"step:s1", "step:s2"} <= ids
    assert {"source": "step:s1", "target": "step:s2", "kind": "depends", "label": "依赖"} in [
        {"source": e["source"], "target": e["target"], "kind": e["kind"], "label": e.get("label")}
        for e in data["edges"]
    ]
    assert data["stats"]["total"] == 2 and data["stats"]["done"] == 1
    assert data["stats"]["progress"] == 0.5


async def test_mindmap_groups_tools_and_files(admin, client):
    agent_id = await _agent(client)
    task = (await client.post("/api/tasks", json={"prompt": "读文件", "agent_ids": [agent_id]})).json()
    done = await _wait(client, task["id"])

    async with session_factory()() as s:
        run = (await s.execute(__import__("sqlalchemy").select(AgentRun).where(AgentRun.task_id == task["id"]))).scalars().first()
        s.add_all([
            TraceEvent(event_id=new_span_id() + "a", trace_id=done["trace_id"], span_id=new_span_id(),
                       task_id=task["id"], run_id=run.id, agent_id=agent_id, event_type="tool.call", name="Read",
                       input_summary='{"path": "src/main.py"}', timestamp=utcnow()),
            TraceEvent(event_id=new_span_id() + "b", trace_id=done["trace_id"], span_id=new_span_id(),
                       task_id=task["id"], run_id=run.id, agent_id=agent_id, event_type="tool.call", name="Read",
                       input_summary='{"path": "src/main.py"}', timestamp=utcnow()),
            TraceEvent(event_id=new_span_id() + "c", trace_id=done["trace_id"], span_id=new_span_id(),
                       task_id=task["id"], run_id=run.id, agent_id=agent_id, event_type="tool.call", name="Bash",
                       input_summary='{"command": "pytest -q"}', timestamp=utcnow()),
        ])
        await s.commit()

    tree = (await client.get(f"/api/graph/task/{task['id']}/mindmap")).json()
    agent_node = next(c for c in tree["children"] if c["type"] == "agent")
    groups = {g["type"]: g for g in agent_node["children"]}
    tools = {c["name"]: c["value"] for c in groups["tool-group"]["children"]}
    assert tools.get("Read ×2") == 2 and tools.get("Bash ×1") == 1
    files = [c["name"] for c in groups["file-group"]["children"] if c["type"] == "file"]
    assert "src/main.py" in files


async def test_graph_404(admin, client):
    assert (await client.get("/api/graph/task/99999")).status_code == 404
    assert (await client.get("/api/graph/task/99999/mindmap")).status_code == 404
