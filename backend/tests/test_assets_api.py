"""Asset management API: MCP servers, skills, prompts."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _McpHandler(BaseHTTPRequestHandler):
    """Minimal MCP server: initialize + tools/list over plain JSON."""

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        method = body.get("method")
        if method == "initialize":
            payload = {"jsonrpc": "2.0", "id": 1,
                       "result": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "serverInfo": {"name": "fake-mcp", "version": "9.9"}}}
        elif method == "tools/list":
            payload = {"jsonrpc": "2.0", "id": 2,
                       "result": {"tools": [{"name": "echo", "description": "回声"},
                                            {"name": "search", "description": "搜索"}]}}
        else:
            payload = {"jsonrpc": "2.0", "id": body.get("id"), "result": {}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence pytest output
        pass


@pytest.fixture
def mcp_url():
    server = HTTPServer(("127.0.0.1", 0), _McpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()


# ------------------------------------------------------------------ MCP servers


async def test_mcp_crud_and_test(admin, client, mcp_url):
    created = await client.post("/api/mcp-servers", json={
        "name": "local-tools", "transport": "streamable-http", "url": mcp_url,
        "description": "本地工具",
    })
    assert created.status_code == 200, created.text
    server_id = created.json()["id"]
    assert created.json()["tool_count"] == 0

    listed = (await client.get("/api/mcp-servers")).json()
    assert [s["name"] for s in listed] == ["local-tools"]

    # duplicate name
    dup = await client.post("/api/mcp-servers", json={"name": "local-tools", "transport": "stdio", "command": "x"})
    assert dup.status_code == 409

    tested = await client.post(f"/api/mcp-servers/{server_id}/test")
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] and body["detail"].startswith("fake-mcp")
    assert [t["name"] for t in body["tools"]] == ["echo", "search"]
    assert body["server"]["tool_count"] == 2
    assert body["server"]["last_check_ok"] is True

    patched = await client.patch(f"/api/mcp-servers/{server_id}", json={"enabled": False, "description": "停用"})
    assert patched.json()["enabled"] is False and patched.json()["config_version"] == 2

    assert (await client.delete(f"/api/mcp-servers/{server_id}")).status_code == 200
    assert (await client.get("/api/mcp-servers")).json() == []


async def test_mcp_validation(admin, client):
    assert (await client.post("/api/mcp-servers", json={"name": "a", "transport": "stdio"})).status_code == 422
    assert (await client.post("/api/mcp-servers", json={"name": "b", "transport": "http", "url": "x"})).status_code == 422
    ok = await client.post("/api/mcp-servers", json={"name": "c", "transport": "stdio", "command": "python"})
    assert ok.status_code == 200


# ----------------------------------------------------------------------- skills


async def test_skill_crud(admin, client):
    created = await client.post("/api/skills", json={
        "name": "code-review", "description": "审查清单", "content": "# 步骤\n1. 看 diff",
        "files": [{"path": "checklist.md", "content": "- [ ] 测试"}], "tags": ["review"],
    })
    assert created.status_code == 200, created.text
    skill_id = created.json()["id"]
    assert created.json()["version"] == 1

    patched = (await client.patch(f"/api/skills/{skill_id}", json={"content": "# 新步骤"})).json()
    assert patched["version"] == 2

    detail = (await client.get(f"/api/skills/{skill_id}")).json()
    assert detail["content"] == "# 新步骤"

    assert (await client.delete(f"/api/skills/{skill_id}")).status_code == 200
    assert (await client.get("/api/skills")).json() == []


# ---------------------------------------------------------------------- prompts


async def test_prompt_render_and_variables(admin, client):
    created = await client.post("/api/prompts", json={
        "name": "bug-report",
        "content": "项目：{{project}}\n问题：{{ issue }}\n复现：{{steps}}",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["variables"] == ["project", "issue", "steps"]
    pid = body["id"]

    rendered = (await client.post(f"/api/prompts/{pid}/render",
                                  json={"variables": {"project": "agentops", "issue": "图表空白"}})).json()
    assert "agentops" in rendered["rendered"] and "{{steps}}" in rendered["rendered"]
    assert rendered["missing"] == ["steps"]

    patched = (await client.patch(f"/api/prompts/{pid}", json={"content": "只问 {{q}}"})).json()
    assert patched["variables"] == ["q"] and patched["version"] == 2


async def test_viewer_cannot_write_assets(client, admin):
    me = (await client.post("/api/auth/setup", json={"username": "v", "password": "viewer-pass-1"})) if False else None
    # create a viewer through the admin API
    created = await client.post("/api/users", json={"username": "viewer1", "password": "viewer-pass-1", "role": "viewer"})
    assert created.status_code == 200, created.text
    token = (await client.post("/api/auth/login", json={"username": "viewer1", "password": "viewer-pass-1"})).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/prompts", json={"name": "x", "content": "y"}, headers=headers)
    assert r.status_code == 403
    assert (await client.get("/api/prompts", headers=headers)).status_code == 200
