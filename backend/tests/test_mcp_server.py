"""MCP server: tools over the platform data, JWT-gated HTTP transport."""

import asyncio

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AgentAdapter, ConnectionResult, Level, RunResult
from app.mcp_server import _ROLE, create_task, dashboard_summary, get_agent, get_task, list_agents, list_events, list_tasks


class QuickAdapter(AgentAdapter):
    type = "test_quick"
    display_name = "Quick"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = []
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        return RunResult(ok=True, output=f"done: {req.prompt}", model="test")


@pytest.fixture(autouse=True)
def _register():
    ADAPTERS["test_quick"] = QuickAdapter()
    _ROLE.set("admin")
    yield
    ADAPTERS.pop("test_quick", None)
    _ROLE.set("admin")


async def _make_agent(client, name="小快"):
    r = await client.post("/api/agents", json={"name": name, "adapter_type": "test_quick", "model": ""})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_tools_expose_agents_and_tasks(admin, client):
    agent_id = await _make_agent(client)

    agents = await list_agents()
    assert any(a["id"] == agent_id and a["adapter_type"] == "test_quick" for a in agents)

    detail = await get_agent(agent_id)
    assert detail["name"] == "小快"

    created = await create_task(prompt="写个 hello", agent_ids=[agent_id], title="MCP 任务")
    assert created["dispatched"] is True

    for _ in range(100):
        task = await get_task(created["id"])
        if task["status"] in ("completed", "failed", "cancelled"):
            break
        await asyncio.sleep(0.1)

    assert task["status"] == "completed", task
    assert task["runs"] and task["runs"][0]["agent_name"] == "小快"
    assert task["runs"][0]["output_text"] == "done: 写个 hello"

    tasks = await list_tasks(limit=5)
    assert any(t["id"] == created["id"] for t in tasks)

    events = await list_events(limit=5, task_id=created["id"])
    assert isinstance(events, list)


async def test_dashboard_summary_shape(admin, client):
    await _make_agent(client)
    summary = await dashboard_summary()
    assert summary["agents"]["total"] >= 1
    assert "by_status" in summary["tasks"]


async def test_viewer_cannot_create_task(admin, client):
    agent_id = await _make_agent(client)
    _ROLE.set("viewer")
    with pytest.raises(PermissionError):
        await create_task(prompt="x", agent_ids=[agent_id])


async def test_create_task_validates_input(admin, client):
    agent_id = await _make_agent(client)
    with pytest.raises(ValueError, match="不存在"):
        await create_task(prompt="x", agent_ids=[agent_id + 999])


async def _mcp_client(app_env):
    """A client that also runs the app lifespan (the MCP session manager needs it)."""
    import httpx

    from app.main import create_app

    app = create_app()
    return app, httpx.ASGITransport(app=app)


async def test_http_transport_requires_token(app_env):
    import httpx

    app, transport = await _mcp_client(app_env)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            r = await c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                             headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401


async def test_http_transport_accepts_valid_token(app_env):
    import httpx

    app, transport = await _mcp_client(app_env)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            setup = await c.post("/api/auth/setup", json={"username": "admin", "password": "admin-pass-123"})
            token = setup.json()["token"]
            r = await c.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "pytest", "version": "1.0"}}},
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                         "Authorization": f"Bearer {token}"},
            )
    assert r.status_code == 200, r.text
    assert "agentops-center" in r.text
