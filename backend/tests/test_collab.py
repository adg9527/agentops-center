"""Phase 2: group chat, manual/auto collaboration, handoffs, approvals, pause/cancel/retry/intervention.

Uses a test-only in-process adapter whose replies are scripted per agent, so orchestration logic is
exercised deterministically without consuming tokens. Real agents are covered in test_real_agents.py.
"""

import asyncio

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Level, RunResult
from app.services.collab import PlanError, extract_handoff, manual_steps, parse_mentions, parse_plan

PROMPTS: dict[str, list[str]] = {}  # agent name -> prompts received
FAILS: dict[str, int] = {}  # agent name -> remaining forced failures


class Scripted(AgentAdapter):
    type = "scripted"
    display_name = "Scripted"
    platform = "test"
    transport = "sdk"
    verified = True
    config_fields = [ConfigField("name", "name"), ConfigField("reply", "reply"), ConfigField("plan", "plan"),
                     ConfigField("delay", "delay", "number", default=0)]
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        name = req.config["name"]
        PROMPTS.setdefault(name, []).append(req.prompt)
        if req.config.get("delay"):
            try:
                await asyncio.wait_for(cancel.wait(), req.config["delay"])
                return RunResult(ok=False, cancelled=True, error="cancelled")
            except TimeoutError:
                pass
        if FAILS.get(name):
            FAILS[name] -= 1
            return RunResult(ok=False, error=f"{name} forced failure")
        if "主控（协调者）" in req.prompt:
            out = req.config["plan"]
        elif "所有子任务已结束" in req.prompt:
            out = f"SUMMARY by {name}"
        else:
            out = (req.config.get("reply") or "done by {name}").replace("{name}", name)
        await emit(AdapterEvent(kind="agent.message", output=out))
        return RunResult(ok=True, output=out, input_tokens=10, output_tokens=5, cost_usd=0.001)


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    ADAPTERS["scripted"] = Scripted()
    PROMPTS.clear()
    FAILS.clear()
    risky = lambda adapter_type, config: ["test risk"] if config.get("risky") else []  # noqa: E731
    monkeypatch.setattr("app.services.collab.risk_warnings", risky)
    monkeypatch.setattr("app.api.collab.risk_warnings", risky)
    yield
    ADAPTERS.pop("scripted", None)


async def _agent(client, name, **config):
    r = await client.post("/api/agents", json={"name": name, "adapter_type": "scripted", "config": {"name": name, **config}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _group(client, members: dict[str, str], coordinator: str | None = None, **kw):
    ids = {}
    for name in members:
        ids[name] = await _agent(client, name, **kw.pop(f"cfg_{name}", {}))
    body = {"name": "team", "agent_ids": list(ids.values())}
    if coordinator:
        body["coordinator_agent_id"] = ids[coordinator]
    body.update(kw)
    c = (await client.post("/api/conversations", json=body)).json()
    for name, role in members.items():
        assert (await client.patch(f"/api/conversations/{c['id']}/members/{ids[name]}", json={"role": role})).status_code == 200
    return c["id"], ids


async def _send(client, cid, content, mode="manual", **kw):
    r = await client.post(f"/api/conversations/{cid}/messages", json={"content": content, "mode": mode, **kw})
    assert r.status_code == 200, r.text
    return r.json()["task"]["id"]


async def _wait(client, tid, until=("completed", "failed", "cancelled"), timeout=20):
    for _ in range(int(timeout * 20)):
        t = (await client.get(f"/api/collab/tasks/{tid}")).json()
        if t["status"] in until and (t["status"] not in ("completed", "failed", "cancelled") or not t["active"]):
            return t
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {tid} stuck: {t['status']} {[(s['step_key'], s['status']) for s in t['steps']]}")


# ------------------------------------------------------------------ pure helpers


def test_parse_mentions_with_spaces():
    ids, all_ = parse_mentions("请 @Claude Code (haiku) 和 @Open 看一下", [(1, "Claude Code (haiku)"), (2, "Open"), (3, "Other")])
    assert ids == [1, 2] and not all_
    assert parse_mentions("@全体 开始", [(1, "A")]) == ([], True)


def test_parse_plan_validation():
    members = {"A": 1, "B": 2}
    plan = parse_plan('好的\n```json\n{"steps":[{"id":"s1","agent":"A","instruction":"x"},'
                      '{"id":"s2","agent":"b","instruction":"y","depends_on":["s1"],"role":"reviewer"}]}\n```', members)
    assert [(p["key"], p["agent_id"], p["depends_on"], p["role"]) for p in plan] == [("s1", 1, [], "executor"), ("s2", 2, ["s1"], "reviewer")]
    for bad, msg in [
        ('{"steps":[{"id":"s1","agent":"Z","instruction":"x"}]}', "不存在"),
        ('{"steps":[{"id":"s1","agent":"A","instruction":"x","depends_on":["s2"]},{"id":"s2","agent":"B","instruction":"y","depends_on":["s1"]}]}', "循环"),
        ('{"steps":[{"id":"s1","agent":"A","instruction":"x","depends_on":["s9"]}]}', "不存在的步骤"),
        ("no json here", "没有找到"),
    ]:
        with pytest.raises(PlanError, match=msg):
            parse_plan(bad, members)


def test_manual_steps_stage_order():
    steps = manual_steps([(3, "reviewer"), (1, "analyst"), (2, "executor"), (4, "executor")])
    assert [(s["agent_id"], s["depends_on"]) for s in steps] == [(1, []), (2, ["s1a"]), (4, ["s1a"]), (3, ["s2a", "s2b"])]


def test_extract_handoff():
    h = extract_handoff('done.\n```handoff\n{"to":"B","reason":"needs tests","done":"impl","remaining":"write tests","files":["a.py"]}\n```')
    assert h == {"to": "B", "reason": "needs tests", "done": "impl", "remaining": "write tests", "files": ["a.py"]}
    assert extract_handoff('```handoff\n{"to":"B"}\n```') is None


# ------------------------------------------------------------------ manual mode


async def test_manual_pipeline_with_handoffs_and_trace(client, admin):
    cid, ids = await _group(client, {"Ana": "analyst", "Exe": "executor", "Rev": "reviewer"},
                            cfg_Ana={"reply": "ANALYSIS-RESULT"})
    tid = await _send(client, cid, "@Ana @Exe @Rev 实现登录功能")
    t = await _wait(client, tid)
    assert t["status"] == "completed", t
    assert [(s["agent_name"], s["role"], s["depends_on"], s["status"]) for s in t["steps"]] == [
        ("Ana", "analyst", [], "completed"), ("Exe", "executor", ["s1a"], "completed"), ("Rev", "reviewer", ["s2a"], "completed")]
    # structured handoffs Ana->Exe->Rev, completed after receivers finished
    assert [(h["from_agent"], h["to_agent"], h["initiated_by"], h["status"]) for h in t["handoffs"]] == [
        ("Ana", "Exe", "platform", "completed"), ("Exe", "Rev", "platform", "completed")]
    h = t["handoffs"][0]
    assert h["result"] == "ANALYSIS-RESULT" and h["remaining_work"] and h["accepted_at"] and h["completed_at"]
    assert "ANALYSIS-RESULT" in PROMPTS["Exe"][0]  # downstream agent actually received upstream output

    msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()
    agent_msgs = [(m["sender_name"], m["content"]) for m in msgs if m["sender_type"] == "agent"]
    # 群聊里每个 Agent 只汇报进度：步骤名 + 结果摘要（完整输出留在任务详情）
    assert [n for n, _ in agent_msgs] == ["Ana", "Exe", "Rev"]
    assert all(content.startswith("✅") and "完成" in content for _, content in agent_msgs)
    assert "ANALYSIS-RESULT" in agent_msgs[0][1]
    brief_msgs = [m for m in msgs if m["sender_type"] == "agent"]
    assert all(m["data"].get("brief") for m in brief_msgs)
    assert msgs[-1]["kind"] == "status" and "已完成" in msgs[-1]["content"]

    events = (await client.get(f"/api/tasks/{tid}/events")).json()
    root_span = next(e["span_id"] for e in events if e["event_type"] == "task.started" and e["task_id"] == tid)
    sub_starts = [e for e in (await client.get(f"/api/events?event_type=task.started")).json() if e["task_id"] != tid]
    assert len(sub_starts) == 3 and all(e["parent_span_id"] == root_span and e["trace_id"] == t["trace_id"] for e in sub_starts)

    listing = (await client.get("/api/tasks")).json()
    assert listing["total"] == 1 and len(listing["items"][0]["runs"]) == 3  # subtasks rolled up, not listed
    assert listing["items"][0]["progress"]["total"] == 3
    assert listing["items"][0]["progress"]["done"] == 3
    assert listing["items"][0]["progress"]["percent"] == 100
    assert (await client.get(f"/api/tasks?agent_id={ids['Exe']}")).json()["total"] == 1


async def test_manual_requires_target_and_member_check(client, admin):
    cid, ids = await _group(client, {"A": "executor"})
    r = await client.post(f"/api/conversations/{cid}/messages", json={"content": "no mention", "mode": "manual"})
    assert r.status_code == 422
    other = await _agent(client, "Outsider")
    r = await client.post(f"/api/conversations/{cid}/messages", json={"content": "x", "mode": "manual", "mentions": [other]})
    assert r.status_code == 422
    r = await client.post(f"/api/conversations/{cid}/messages", json={"content": "just chatting", "mode": "chat"})
    assert r.status_code == 200 and r.json()["task"] is None


async def test_mention_all_and_explicit_assignments(client, admin):
    cid, ids = await _group(client, {"A": "executor", "B": "executor"})
    t = await _wait(client, await _send(client, cid, "@全体 各自检查一遍"))
    assert t["status"] == "completed" and {s["agent_name"] for s in t["steps"]} == {"A", "B"}
    assert all(s["depends_on"] == [] for s in t["steps"])  # same stage -> parallel
    t = await _wait(client, await _send(client, cid, "按分工执行", assignments=[{"agent_id": ids["A"], "role": "reviewer"},
                                                                                  {"agent_id": ids["B"], "role": "analyst"}]))
    assert [(s["agent_name"], s["role"]) for s in t["steps"]] == [("B", "analyst"), ("A", "reviewer")]


# ------------------------------------------------------------------ auto mode


PLAN = ('```json\n{"steps":[{"id":"s1","agent":"Dev","role":"executor","title":"写代码","instruction":"implement"},'
        '{"id":"s2","agent":"QA","role":"reviewer","title":"测试","instruction":"test it","depends_on":["s1"]}]}\n```')


async def test_auto_mode_coordinator_plans_and_summarizes(client, admin):
    cid, ids = await _group(client, {"Boss": "analyst", "Dev": "executor", "QA": "reviewer"}, coordinator="Boss", cfg_Boss={"plan": PLAN})
    t = await _wait(client, await _send(client, cid, "做一个计算器", mode="auto"))
    assert t["status"] == "completed", t
    assert [(s["step_key"], s["kind"], s["agent_name"], s["status"]) for s in t["steps"]] == [
        ("plan", "plan", "Boss", "completed"), ("s1", "work", "Dev", "completed"),
        ("s2", "work", "QA", "completed"), ("summary", "summary", "Boss", "completed")]
    pairs = [(h["from_agent"], h["to_agent"]) for h in t["handoffs"]]
    assert ("Boss", "Dev") in pairs and ("Boss", "QA") in pairs and ("Dev", "QA") in pairs
    assert "done by Dev" in PROMPTS["Boss"][1]  # summary got member results
    msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()
    assert any("主控已拆分任务" in m["content"] for m in msgs)
    assert any(m["content"] == "SUMMARY by Boss" for m in msgs)


async def test_auto_mode_invalid_plan_fails_cleanly(client, admin):
    cid, _ = await _group(client, {"Boss": "analyst", "Dev": "executor"}, coordinator="Boss",
                          cfg_Boss={"plan": '{"steps":[{"agent":"Ghost","instruction":"x"}]}'})
    t = await _wait(client, await _send(client, cid, "do it", mode="auto"))
    assert t["status"] == "failed" and "Ghost" in t["steps"][0]["error"]


async def test_auto_without_coordinator_falls_back_to_roles(client, admin):
    cid, _ = await _group(client, {"A": "analyst", "B": "executor"})
    t = await _wait(client, await _send(client, cid, "do it", mode="auto"))
    assert t["status"] == "completed" and [s["agent_name"] for s in t["steps"]] == ["A", "B"]


async def test_plan_approval_approve_and_reject(client, admin):
    cid, _ = await _group(client, {"Boss": "analyst", "Dev": "executor", "QA": "reviewer"}, coordinator="Boss",
                          cfg_Boss={"plan": PLAN}, require_plan_approval=True)
    tid = await _send(client, cid, "x", mode="auto")
    t = await _wait(client, tid, until=("waiting_approval",))
    pending = [a for a in t["approvals"] if a["status"] == "pending"]
    assert len(pending) == 1 and pending[0]["kind"] == "plan" and len(pending[0]["detail"]["steps"]) == 2
    assert "Dev" not in PROMPTS  # nothing executed before approval
    assert (await client.post(f"/api/approvals/{pending[0]['id']}/approve", json={"comment": "ok"})).status_code == 200
    assert (await _wait(client, tid))["status"] == "completed"

    tid2 = await _send(client, cid, "y", mode="auto")
    t = await _wait(client, tid2, until=("waiting_approval",))
    aid = next(a["id"] for a in t["approvals"] if a["status"] == "pending")
    await client.post(f"/api/approvals/{aid}/reject", json={"comment": "no"})
    t = await _wait(client, tid2)
    assert t["status"] == "cancelled" and all(s["status"] in ("completed", "skipped", "cancelled") for s in t["steps"])
    assert (await client.post(f"/api/approvals/{aid}/approve", json={})).status_code == 409


async def test_high_risk_step_needs_admin_approval(client, admin):
    cid, ids = await _group(client, {"Safe": "analyst", "Risky": "executor"}, cfg_Risky={"risky": True})
    tid = await _send(client, cid, "@Safe @Risky go")
    t = await _wait(client, tid, until=("waiting_approval",))
    step = next(s for s in t["steps"] if s["agent_name"] == "Risky")
    assert step["status"] == "waiting_approval" and "Risky" not in PROMPTS
    approval = next(a for a in t["approvals"] if a["kind"] == "high_risk_step")
    await client.post("/api/users", json={"username": "op", "password": "op-pass-1234", "role": "operator"})
    tok = (await client.post("/api/auth/login", json={"username": "op", "password": "op-pass-1234"})).json()["token"]
    r = await client.post(f"/api/approvals/{approval['id']}/approve", json={}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403  # operators can't approve high-risk steps
    await client.post(f"/api/approvals/{approval['id']}/reject", json={"comment": "too risky"})
    t = await _wait(client, tid)
    assert t["status"] == "failed" and "too risky" in next(s for s in t["steps"] if s["agent_name"] == "Risky")["error"]


# ------------------------------------------------------------------ agent-initiated handoff


async def test_agent_initiated_handoff(client, admin):
    reply = 'impl done\n```handoff\n{"to":"Tester","reason":"需要测试","done":"写完了 calc.py","remaining":"为 calc.py 写单元测试","files":["calc.py"]}\n```'
    cid, _ = await _group(client, {"Coder": "executor", "Tester": "reviewer"}, cfg_Coder={"reply": reply})
    t = await _wait(client, await _send(client, cid, "@Coder 写个计算器"))
    assert t["status"] == "completed"
    assert [(s["step_key"], s["agent_name"], s["origin"], s["status"]) for s in t["steps"]] == [
        ("s1a", "Coder", "user", "completed"), ("h1", "Tester", "handoff", "completed")]
    h = next(h for h in t["handoffs"] if h["initiated_by"] == "agent")
    assert (h["from_agent"], h["to_agent"], h["reason"], h["files"], h["status"]) == ("Coder", "Tester", "需要测试", ["calc.py"], "completed")
    assert "为 calc.py 写单元测试" in PROMPTS["Tester"][0] and "写完了 calc.py" in PROMPTS["Tester"][0]
    events = (await client.get(f"/api/tasks/{t['id']}/events")).json()
    assert any(e["event_type"] == "handoff.created" for e in events)


# ------------------------------------------------------------------ control: pause / resume / cancel / retry / intervene


async def test_pause_resume(client, admin):
    cid, _ = await _group(client, {"Slow": "analyst", "Next": "executor"}, cfg_Slow={"delay": 1})
    tid = await _send(client, cid, "@Slow @Next go")
    await asyncio.sleep(0.3)
    assert (await client.post(f"/api/collab/tasks/{tid}/pause")).status_code == 200
    t = await _wait(client, tid, until=("paused",))
    assert [s["status"] for s in t["steps"]] == ["completed", "pending"]  # current step finished, next held
    await client.post(f"/api/collab/tasks/{tid}/intervene", json={"note": "请用中文回答"})
    await client.post(f"/api/collab/tasks/{tid}/resume")
    t = await _wait(client, tid)
    assert t["status"] == "completed"
    assert "请用中文回答" in PROMPTS["Next"][0]  # intervention injected into later steps


async def test_cancel_kills_running_step(client, admin):
    cid, _ = await _group(client, {"Slow": "analyst", "Next": "executor"}, cfg_Slow={"delay": 30})
    tid = await _send(client, cid, "@Slow @Next go")
    await asyncio.sleep(0.3)
    await client.post(f"/api/collab/tasks/{tid}/cancel")
    t = await _wait(client, tid, timeout=10)
    assert t["status"] == "cancelled" and [s["status"] for s in t["steps"]] == ["cancelled", "cancelled"]
    assert "Next" not in PROMPTS


async def test_retry_and_reassign_failed_step(client, admin):
    cid, ids = await _group(client, {"A": "analyst", "B": "executor", "C": "executor"})
    FAILS["A"] = 1
    tid = await _send(client, cid, "@A @B go")
    t = await _wait(client, tid)
    assert t["status"] == "failed" and [s["status"] for s in t["steps"]] == ["failed", "skipped"]
    await client.post(f"/api/collab/steps/{t['steps'][0]['id']}/retry")
    t = await _wait(client, tid)
    assert t["status"] == "completed" and [s["status"] for s in t["steps"]] == ["completed", "completed"]

    FAILS["B"] = 1
    tid = await _send(client, cid, "@B solo")
    t = await _wait(client, tid)
    assert t["status"] == "failed"
    await client.post(f"/api/collab/steps/{t['steps'][0]['id']}/reassign", json={"agent_id": ids["C"]})
    t = await _wait(client, tid)
    assert t["status"] == "completed" and t["steps"][0]["agent_name"] == "C"
    assert any(h["initiated_by"] == "user" and h["to_agent"] == "C" for h in t["handoffs"])


async def test_add_step_and_members(client, admin):
    cid, ids = await _group(client, {"A": "executor"})
    b = await _agent(client, "B")
    assert (await client.post(f"/api/conversations/{cid}/members", json={"agent_id": b, "role": "reviewer"})).status_code == 200
    assert (await client.post(f"/api/conversations/{cid}/members", json={"agent_id": b})).status_code == 409
    tid = await _send(client, cid, "@A go")
    t = await _wait(client, tid)
    await client.post(f"/api/collab/tasks/{tid}/steps", json={"agent_id": b, "title": "补充审查", "instruction": "review", "depends_on": ["s1a"]})
    t = await _wait(client, tid)
    assert t["status"] == "completed" and t["steps"][-1]["agent_name"] == "B" and t["steps"][-1]["status"] == "completed"
    await client.delete(f"/api/conversations/{cid}/members/{b}")
    conv = (await client.get(f"/api/conversations/{cid}")).json()
    assert [m["name"] for m in conv["members"]] == ["A"]
    convs = (await client.get("/api/conversations")).json()
    assert convs[0]["member_count"] == 1 and convs[0]["last_message"]
