"""API + task runner integration tests (no tokens consumed).

A test-only adapter wraps tests/fake_cli.py so the real CLI runner, trace pipeline, cancel and timeout
paths are exercised end-to-end. The generic HTTP adapter is tested against a real local HTTP server.
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Level, RunResult
from app.adapters.cli_runner import run_streaming

FAKE = Path(__file__).parent / "fake_cli.py"


class ScriptAdapter(AgentAdapter):
    type = "test_script"
    display_name = "Test Script"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = [ConfigField("mode", "mode", default="ok"), ConfigField("token", "token", secret=True)]
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok", version="1.0")

    async def run(self, req, emit, cancel):
        out = RunResult(ok=False)

        async def on_line(line):
            try:
                d = json.loads(line)
            except ValueError:
                return
            if d["type"] == "tool":
                await emit(AdapterEvent(kind="tool.call", name=d["name"], input=json.dumps(d["input"]), call_id=d["id"]))
            else:
                await emit(AdapterEvent(kind="tool.result", name="Read", output="ok", call_id=d["id"]))
                await emit(AdapterEvent(kind="agent.message", output=d["text"]))
                out.ok, out.output = True, d["text"]

        o = await run_streaming([sys.executable, str(FAKE), req.config["mode"]], cwd=req.workdir, on_line=on_line,
                                cancel=cancel, timeout_sec=req.timeout_sec, stdin_text=req.prompt)
        out.cancelled, out.timed_out = o.cancelled, o.timed_out
        if o.exit_code != 0 or o.cancelled or o.timed_out:
            out.ok, out.error = False, f"exit={o.exit_code} {o.stderr_tail}"
        return out


@pytest.fixture(autouse=True)
def _register():
    ADAPTERS["test_script"] = ScriptAdapter()
    yield
    ADAPTERS.pop("test_script", None)


async def _wait_task(client, task_id, timeout=30):
    for _ in range(int(timeout * 10)):
        t = (await client.get(f"/api/tasks/{task_id}")).json()
        if t["status"] in ("completed", "failed", "cancelled") and not t["active"]:
            return t
        await asyncio.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish: {t}")


async def _agent(client, **kw):
    body = {"name": "script", "adapter_type": "test_script", "config": {"mode": "ok"}, **kw}
    r = await client.post("/api/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_auth_flow(client):
    assert (await client.get("/api/auth/setup-status")).json() == {"needs_setup": True}
    assert (await client.get("/api/agents")).status_code == 401
    r = await client.post("/api/auth/setup", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200
    assert (await client.post("/api/auth/setup", json={"username": "x2", "password": "admin-pass-123"})).status_code == 409
    assert (await client.post("/api/auth/login", json={"username": "admin", "password": "wrong-pass"})).status_code == 401
    tok = (await client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})).json()["token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    assert (await client.get("/api/auth/me")).json()["role"] == "admin"
    # viewer cannot mutate
    await client.post("/api/users", json={"username": "viewer1", "password": "viewer-pass-1", "role": "viewer"})
    vt = (await client.post("/api/auth/login", json={"username": "viewer1", "password": "viewer-pass-1"})).json()["token"]
    r = await client.post("/api/agents", json={"name": "x", "adapter_type": "test_script"},
                          headers={"Authorization": f"Bearer {vt}"})
    assert r.status_code == 403
    actions = [a["action"] for a in (await client.get("/api/audit-logs")).json()["items"]]
    assert "auth.login_failed" in actions and "system.setup" in actions


async def test_agent_crud_secrets_never_returned(client, admin):
    adapters = {a["type"]: a for a in (await client.get("/api/adapters")).json()}
    assert adapters["codex"]["verified"] is False
    assert adapters["http"]["capabilities"]["tool_events"] == "unsupported"

    a = await _agent(client, secrets={"token": "tok-very-secret-value"})
    assert a["secret_keys_set"] == ["token"]
    assert "tok-very-secret-value" not in json.dumps(a)
    assert "tok-very-secret-value" not in json.dumps((await client.get("/api/agents")).json())
    assert "tok-very-secret-value" not in json.dumps((await client.get("/api/audit-logs")).json())

    r = await client.patch(f"/api/agents/{a['id']}", json={"config": {"mode": "fail"}, "secrets": {"token": None}})
    assert r.json()["config"]["mode"] == "fail" and r.json()["secret_keys_set"] == []
    assert r.json()["config_version"] == 2

    t = (await client.post(f"/api/agents/{a['id']}/test")).json()
    assert t["ok"] and t["agent"]["health"] == "online"

    assert (await client.post(f"/api/agents/{a['id']}/disable")).json()["enabled"] is False
    r = await client.post("/api/tasks", json={"prompt": "hi", "agent_ids": [a["id"]]})
    assert r.status_code == 422 and "停用" in r.json()["detail"]
    assert (await client.post(f"/api/agents/{a['id']}/enable")).json()["enabled"] is True

    assert (await client.delete(f"/api/agents/{a['id']}")).json() == {"ok": True}
    assert (await client.get(f"/api/agents/{a['id']}")).status_code == 404
    assert (await client.get("/api/agents")).json() == []


async def test_task_success_trace_and_redaction(client, admin):
    a1 = await _agent(client, name="a1")
    a2 = await _agent(client, name="a2")
    r = await client.post("/api/tasks", json={"prompt": "hello world", "agent_ids": [a1["id"], a2["id"]]})
    assert r.status_code == 200
    t = await _wait_task(client, r.json()["id"])
    assert t["status"] == "completed"
    assert len(t["runs"]) == 2 and all(run["status"] == "completed" for run in t["runs"])
    assert "echo: hello world" in t["runs"][0]["output_text"]
    assert "secretvalue123" not in t["runs"][0]["output_text"]  # redacted before persistence

    events = (await client.get(f"/api/tasks/{t['id']}/events")).json()
    types = [e["event_type"] for e in events]
    assert types[0] == "task.started" and types[-1] == "task.completed"
    assert types.count("agent.run.started") == 2 and types.count("tool.call") == 2
    assert {e["trace_id"] for e in events} == {t["trace_id"]}  # both agents chained in one trace
    call = next(e for e in events if e["event_type"] == "tool.call")
    res = next(e for e in events if e["event_type"] == "tool.result" and e["run_id"] == call["run_id"])
    assert call["span_id"] == res["span_id"]
    run_span = next(e for e in events if e["event_type"] == "agent.run.started" and e["run_id"] == call["run_id"])
    assert call["parent_span_id"] == run_span["span_id"]
    assert all("secretvalue123" not in (e["output_summary"] or "") for e in events)

    dash = (await client.get("/api/dashboard/summary")).json()
    assert dash["tasks_completed"] == 1 and dash["runs_total"] == 2 and dash["run_success_rate"] == 1.0
    assert dash["not_implemented"]["mcp_count"]["phase"] == 4
    stats = (await client.get(f"/api/agents/{a1['id']}/stats")).json()
    assert stats["total"] == 1 and stats["success_rate"] == 1.0
    assert len((await client.get(f"/api/agents/{a1['id']}/runs")).json()) == 1


async def test_task_failure_and_retry(client, admin):
    a = await _agent(client, config={"mode": "fail"}, max_retries=1)
    t = await _wait_task(client, (await client.post("/api/tasks", json={"prompt": "x", "agent_ids": [a["id"]]})).json()["id"])
    assert t["status"] == "failed"
    assert [r["attempt"] for r in t["runs"]] == [1, 2]
    assert "boom" in t["runs"][0]["error"]


async def test_task_cancel_kills_process(client, admin):
    a = await _agent(client, config={"mode": "slow"})
    tid = (await client.post("/api/tasks", json={"prompt": "x", "agent_ids": [a["id"]]})).json()["id"]
    for _ in range(100):
        evs = (await client.get(f"/api/tasks/{tid}/events")).json()
        if any(e["event_type"] == "tool.call" for e in evs):
            break
        await asyncio.sleep(0.1)
    assert (await client.post(f"/api/tasks/{tid}/cancel")).json() == {"ok": True}
    t = await _wait_task(client, tid, timeout=20)
    assert t["status"] == "cancelled" and t["runs"][0]["status"] == "cancelled"
    assert t["runs"][0]["duration_ms"] < 30_000


async def test_task_timeout(client, admin):
    a = await _agent(client, config={"mode": "slow"}, timeout_sec=10)
    t = await _wait_task(client, (await client.post("/api/tasks", json={"prompt": "x", "agent_ids": [a["id"]]})).json()["id"], 40)
    assert t["status"] == "failed" and t["runs"][0]["status"] == "timeout"


async def test_http_adapter_against_real_server(client, admin):
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["auth"] = self.headers.get("Authorization")
            payload = json.dumps({"data": {"answer": f"got {body['q']}"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        a = (await client.post("/api/agents", json={
            "name": "my-http-agent", "adapter_type": "http",
            "config": {"url": f"{base}/run", "health_url": f"{base}/health", "prompt_field": "q", "output_path": "data.answer"},
            "secrets": {"auth_value": "Bearer abc123secret"}})).json()
        assert (await client.post(f"/api/agents/{a['id']}/test")).json()["ok"]
        t = await _wait_task(client, (await client.post("/api/tasks", json={"prompt": "ping", "agent_ids": [a["id"]]})).json()["id"])
        assert t["status"] == "completed" and t["runs"][0]["output_text"] == "got ping"
        assert seen["auth"] == "Bearer abc123secret"
    finally:
        srv.shutdown()
