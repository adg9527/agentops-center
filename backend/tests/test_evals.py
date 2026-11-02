"""Evaluation loop: scoring, thresholds, rounds, prompt/skill injection."""

import asyncio

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AdapterEvent, AgentAdapter, ConnectionResult, Level, RunResult


class OkAdapter(AgentAdapter):
    type = "test_eval_ok"
    display_name = "Eval OK"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = []
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        CaptureAdapter.last_prompt = req.prompt
        await emit(AdapterEvent(kind="tool.call", name="Read", input='{"path":"a.py"}', call_id="t1"))
        await emit(AdapterEvent(kind="tool.result", name="Read", output="ok", call_id="t1"))
        return RunResult(ok=True, output="做完了：结果 OK", model="test")


class CaptureAdapter(OkAdapter):
    type = "test_eval_capture"
    last_prompt = ""


class FailAdapter(AgentAdapter):
    type = "test_eval_fail"
    display_name = "Eval Fail"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = []
    capabilities = {"send_task": Level.supported}

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        return RunResult(ok=False, error="模型拒绝执行")


@pytest.fixture(autouse=True)
def _register():
    ADAPTERS["test_eval_ok"] = OkAdapter()
    ADAPTERS["test_eval_capture"] = CaptureAdapter()
    ADAPTERS["test_eval_fail"] = FailAdapter()
    CaptureAdapter.last_prompt = ""
    yield
    for key in ("test_eval_ok", "test_eval_capture", "test_eval_fail"):
        ADAPTERS.pop(key, None)


async def _agent(client, adapter="test_eval_ok", name="评测Agent"):
    r = await client.post("/api/agents", json={"name": name, "adapter_type": adapter, "model": ""})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _wait_runs(client, suite_id, *, at_least=1, timeout=30):
    deadline = timeout * 10
    while deadline > 0:
        runs = (await client.get(f"/api/evals/{suite_id}/runs")).json()
        done = [r for r in runs if r["status"] != "running"]
        if len(done) >= at_least and not any(r["status"] == "running" for r in runs):
            return runs
        await asyncio.sleep(0.1)
        deadline -= 1
    raise AssertionError(f"评测没有在 {timeout}s 内完成")


BASE = {"thresholds": {"completion": 1.0, "tool_use": 0.0, "keyword": 0.8, "max_avg_tokens": 0, "max_avg_ms": 0},
        "max_rounds": 3}


async def test_suite_passes_on_first_round(admin, client):
    agent_id = await _agent(client)
    suite = (await client.post("/api/evals", json={
        **BASE, "name": "smoke", "agent_ids": [agent_id],
        "cases": [{"prompt": "写一个 hello", "expect": "OK"}],
    })).json()

    started = await client.post(f"/api/evals/{suite['id']}/run")
    assert started.status_code == 200, started.text

    runs = await _wait_runs(client, suite["id"])
    assert len(runs) == 1
    run = runs[0]
    assert run["passed"] is True
    assert run["metrics"]["completion"] == 1.0
    assert run["metrics"]["tool_use"] == 1.0
    assert run["metrics"]["keyword"] == 1.0
    assert run["task_ids"]

    detail = (await client.get(f"/api/evals/runs/{run['id']}")).json()
    assert detail["detail"][0]["agents"][0]["tool_calls"] == 1


async def test_failing_suite_loops_until_round_budget(admin, client):
    agent_id = await _agent(client, "test_eval_fail", "坏Agent")
    suite = (await client.post("/api/evals", json={
        **BASE, "max_rounds": 2, "name": "loop", "agent_ids": [agent_id],
        "cases": [{"prompt": "做点事"}],
    })).json()

    await client.post(f"/api/evals/{suite['id']}/run")
    runs = await _wait_runs(client, suite["id"], at_least=2)

    assert len(runs) == 2
    assert all(r["passed"] is False for r in runs)
    # 第二轮带上了上一轮的反馈
    second = min(runs, key=lambda r: r["round"])
    assert second["round"] == 1
    assert runs[0]["hints"] or runs[1]["hints"]


async def test_prompt_and_skill_injected_into_case(admin, client):
    agent_id = await _agent(client, "test_eval_capture", "捕获Agent")
    skill = (await client.post("/api/skills", json={"name": "eval-skill", "content": "技能正文：先读再写"})).json()
    prompt = (await client.post("/api/prompts", json={"name": "eval-prompt", "content": "模板正文：遵守规范"})).json()

    suite = (await client.post("/api/evals", json={
        **BASE, "name": "inject", "agent_ids": [agent_id], "skill_ids": [skill["id"]],
        "prompt_ids": [prompt["id"]], "cases": [{"prompt": "执行用例一"}],
    })).json()
    await client.post(f"/api/evals/{suite['id']}/run")
    await _wait_runs(client, suite["id"])

    text = CaptureAdapter.last_prompt
    assert "模板正文：遵守规范" in text
    assert "技能正文：先读再写" in text
    assert "执行用例一" in text


async def test_suite_validation(admin, client):
    agent_id = await _agent(client)
    assert (await client.post("/api/evals", json={**BASE, "name": "x", "agent_ids": [], "cases": [{"prompt": "p"}]})).status_code == 422
    assert (await client.post("/api/evals", json={**BASE, "name": "y", "agent_ids": [agent_id], "cases": []})).status_code == 422

    suite = (await client.post("/api/evals", json={**BASE, "name": "dup", "agent_ids": [agent_id],
                                                   "cases": [{"prompt": "p"}]})).json()
    dup = await client.post("/api/evals", json={**BASE, "name": "dup", "agent_ids": [agent_id],
                                                "cases": [{"prompt": "p"}]})
    assert dup.status_code == 409
    assert (await client.delete(f"/api/evals/{suite['id']}")).status_code == 200
    assert (await client.get(f"/api/evals/{suite['id']}/runs")).status_code == 404
