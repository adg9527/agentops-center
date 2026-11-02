"""End-to-end tests against REAL locally installed agents (consume tokens / quota).

Run explicitly:  uv run pytest -m real_agent -s
"""

import asyncio
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_agent

PROMPT = "Read the file note.txt in the current directory and reply with its exact content only."


async def _run(client, adapter_type, model, config=None):
    a = (await client.post("/api/agents", json={"name": adapter_type, "adapter_type": adapter_type, "model": model,
                                                  "config": config or {}, "timeout_sec": 240})).json()
    test = (await client.post(f"/api/agents/{a['id']}/test")).json()
    assert test["ok"], test
    tid = (await client.post("/api/tasks", json={"prompt": PROMPT, "agent_ids": [a["id"]]})).json()["id"]
    task = (await client.get(f"/api/tasks/{tid}")).json()
    # Seed the isolated workspace; the CLI takes seconds to boot and call the model before any Read.
    ws = Path(task["workspace_dir"])
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "note.txt").write_text("AGENTOPS-MAGIC-42", encoding="utf-8")
    for _ in range(2400):
        task = (await client.get(f"/api/tasks/{tid}")).json()
        if task["status"] in ("completed", "failed", "cancelled") and not task["active"]:
            break
        await asyncio.sleep(0.1)
    events = (await client.get(f"/api/tasks/{tid}/events")).json()
    for e in events:
        if e["event_type"].startswith("tool."):
            print("  ", e["event_type"], e["name"], (e["input_summary"] or "")[:200], (e["output_summary"] or "")[:120])
    print("   workspace:", task["workspace_dir"])
    return task, events


@pytest.mark.skipif(not shutil.which("claude"), reason="claude not installed")
async def test_claude_code_real(client, admin):
    task, events = await _run(client, "claude_code", "haiku")
    run = task["runs"][0]
    print("\nclaude:", run["status"], run["output_text"], run["cost_usd"], run["input_tokens"], run["output_tokens"])
    assert task["status"] == "completed", run["error"]
    assert "AGENTOPS-MAGIC-42" in run["output_text"]
    assert any(e["event_type"] == "tool.call" and e["name"] == "Read" for e in events)
    assert run["cost_usd"] and run["input_tokens"]


@pytest.mark.skipif(not shutil.which("opencode"), reason="opencode not installed")
async def test_opencode_real(client, admin):
    task, events = await _run(client, "opencode", "opencode/big-pickle")
    run = task["runs"][0]
    print("\nopencode:", run["status"], run["output_text"], run["input_tokens"], run["output_tokens"])
    assert task["status"] == "completed", run["error"]
    assert "AGENTOPS-MAGIC-42" in run["output_text"]
    assert any(e["event_type"] == "tool.call" for e in events)


@pytest.mark.skipif(not (shutil.which("claude") and shutil.which("opencode")), reason="needs claude and opencode")
async def test_real_auto_collaboration(client, admin):
    """Claude Code coordinates (plan + summary) and reviews; OpenCode writes a file that the reviewer must read."""
    claude = (await client.post("/api/agents", json={
        "name": "Claude", "adapter_type": "claude_code", "model": "haiku", "timeout_sec": 300,
        "capability_tags": ["review", "planning"], "description": "只读：负责规划、审查与汇总"})).json()
    oc = (await client.post("/api/agents", json={
        "name": "OpenCode", "adapter_type": "opencode", "model": "opencode/big-pickle", "timeout_sec": 300,
        "capability_tags": ["coding", "write-files"], "description": "可在工作目录写文件，负责实现",
        "config": {"permission": {"edit": "allow", "bash": "deny", "webfetch": "deny"}}})).json()
    conv = (await client.post("/api/conversations", json={"name": "real-team", "agent_ids": [claude["id"], oc["id"]],
                                                          "coordinator_agent_id": claude["id"]})).json()
    await client.patch(f"/api/conversations/{conv['id']}/members/{claude['id']}", json={"role": "reviewer"})
    tid = (await client.post(f"/api/conversations/{conv['id']}/messages", json={"mode": "auto", "content":
        "Two steps. Step 1 (OpenCode): create a file named hello.txt in the current directory containing exactly "
        "the text COLLAB-OK. Step 2 (Claude, depends on step 1): read hello.txt and confirm its exact content."})).json()["task"]["id"]

    # OpenCode has edit=allow -> high-risk -> its step waits for admin approval.
    for _ in range(3000):
        t = (await client.get(f"/api/collab/tasks/{tid}")).json()
        for a in t["approvals"]:
            if a["status"] == "pending":
                await client.post(f"/api/approvals/{a['id']}/approve", json={"comment": "test"})
        if t["status"] in ("completed", "failed", "cancelled") and not t["active"]:
            break
        await asyncio.sleep(0.2)
    for st in t["steps"]:
        print(f"\n  [{st['step_key']}] {st['agent_name']} {st['kind']} {st['status']} :: {(st['output_text'] or st['error'] or '')[:200]!r}")
    for h in t["handoffs"]:
        print(f"  handoff {h['from_agent']} -> {h['to_agent']} ({h['initiated_by']}) {h['status']} files={h['files']}")
    assert t["status"] == "completed", t["error"]
    assert (Path(t["workspace_dir"]) / "hello.txt").read_text(encoding="utf-8").strip() == "COLLAB-OK"
    kinds = [s["kind"] for s in t["steps"]]
    assert kinds[0] == "plan" and kinds[-1] == "summary" and kinds.count("work") >= 2
    review = [s for s in t["steps"] if s["kind"] == "work" and s["agent_name"] == "Claude"]
    assert review and "COLLAB-OK" in review[0]["output_text"]
    assert any(h["from_agent"] == "OpenCode" and h["to_agent"] == "Claude" and "hello.txt" in h["files"] for h in t["handoffs"])
    assert any(a["kind"] == "high_risk_step" and a["status"] == "approved" for a in t["approvals"])


@pytest.mark.skipif(not __import__("os").environ.get("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
async def test_langchain_deepseek_real(client, admin):
    task, events = await _run(client, "langchain", "deepseek-flash")
    run = task["runs"][0]
    print("\nlangchain:", run["status"], run["output_text"], run["input_tokens"], run["output_tokens"])
    assert task["status"] == "completed", run["error"]
    assert "AGENTOPS-MAGIC-42" in run["output_text"]
    assert any(e["event_type"] == "tool.call" and e["name"] == "read_file" for e in events)
    assert any(e["event_type"] == "model.call" and e["metadata"].get("input_tokens") for e in events)


@pytest.mark.skipif(not (shutil.which("opencode") and __import__("os").environ.get("DEEPSEEK_API_KEY")), reason="needs opencode + DeepSeek")
async def test_real_langchain_coordinates_opencode(client, admin):
    """The user's setup: OpenCode with a DeepSeek model, coordinated by a LangChain (DeepSeek) agent."""
    lc = (await client.post("/api/agents", json={"name": "LangChain主控", "adapter_type": "langchain", "model": "deepseek-flash",
                                                  "timeout_sec": 300, "description": "负责规划与汇总"})).json()
    bad = await client.post("/api/agents", json={"name": "OpenCode", "adapter_type": "opencode", "model": "deepseek-v4.1-flash"})
    assert bad.status_code == 422 and "opencode/deepseek-v4.1-flash" in bad.json()["detail"]
    oc = (await client.post("/api/agents", json={"name": "OpenCode", "adapter_type": "opencode", "model": "opencode/deepseek-v4.1-flash",
                                                  "timeout_sec": 300, "description": "负责回答与实现"})).json()
    conv = (await client.post("/api/conversations", json={"name": "lc-team", "agent_ids": [lc["id"], oc["id"]],
                                                          "coordinator_agent_id": lc["id"]})).json()
    tid = (await client.post(f"/api/conversations/{conv['id']}/messages", json={"mode": "auto", "content":
        "只有一个子任务，交给 OpenCode：用一句话说明 Python 列表推导式是什么。"})).json()["task"]["id"]
    for _ in range(3000):
        t = (await client.get(f"/api/collab/tasks/{tid}")).json()
        if t["status"] in ("completed", "failed", "cancelled") and not t["active"]:
            break
        await asyncio.sleep(0.2)
    for st in t["steps"]:
        print(f"\n  [{st['step_key']}] {st['agent_name']} {st['status']} :: {(st['output_text'] or st['error'] or '')[:150]!r}")
    assert t["status"] == "completed", t["error"]
    assert any(s["agent_name"] == "OpenCode" and s["status"] == "completed" for s in t["steps"])
