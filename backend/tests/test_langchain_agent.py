"""LangChain Agent adapter: tools, workspace confinement, trace events, platform integration.

A scripted chat model (test-only) stands in for the provider so the real LangChain create_agent loop,
tool execution and callbacks run deterministically. Real DeepSeek runs are in test_real_agents.py.
"""

import asyncio
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.adapters import ADAPTERS
from app.adapters.base import RunRequest
from app.adapters.langchain_agent import LangChainAdapter, WorkspaceError, _resolve, build_tools
from app.api.agents import risk_warnings


class ScriptedChat(BaseChatModel):
    replies: list
    delay: float = 0

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        if self.delay:
            await asyncio.sleep(self.delay)
        msg = self.replies.pop(0) if self.replies else AIMessage(content="(no more replies)")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _ai(content="", calls=None, i=10, o=5):
    return AIMessage(content=content, tool_calls=[{"name": n, "args": a, "id": f"c{k}"} for k, (n, a) in enumerate(calls or [])],
                     usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o})


class Scripted(LangChainAdapter):
    def __init__(self, replies, delay=0):
        self._model = ScriptedChat(replies=replies, delay=delay)

    def build_model(self, req, key):
        return self._model


def _req(tmp_path, **cfg):
    a = LangChainAdapter()
    return RunRequest(prompt="go", workdir=tmp_path, config={**a.defaults(), **cfg}, secrets={"api_key": "k"},
                      model="scripted-model", timeout_sec=30)


async def _collect(adapter, req, cancel=None):
    evs = []

    async def emit(e):
        evs.append(e)

    r = await adapter.run(req, emit, cancel or asyncio.Event())
    return r, evs


def test_resolve_confines_to_workspace(tmp_path):
    assert _resolve(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()
    for bad in ["..", "../x", "a/../../x", str(Path(tmp_path).anchor) + "Windows"]:
        with pytest.raises(WorkspaceError):
            _resolve(tmp_path, bad)


def test_tools_behaviour(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    t = {x.name: x for x in build_tools(tmp_path, ["list_files", "read_file", "search_text", "write_file"])}
    assert "src/a.py" in t["list_files"].invoke({"path": "."})
    assert "return 42" in t["read_file"].invoke({"path": "src/a.py"})
    assert "src/a.py:2:" in t["search_text"].invoke({"pattern": r"return \d+"})
    assert "已写入" in t["write_file"].invoke({"path": "out/n.txt", "content": "hi"})
    assert (tmp_path / "out" / "n.txt").read_text(encoding="utf-8") == "hi"
    assert "路径越界" in t["read_file"].invoke({"path": "../secret"})  # returned to the model, not raised
    assert [x.name for x in build_tools(tmp_path, ["read_file"])] == ["read_file"]


async def test_agent_loop_tools_and_trace(tmp_path):
    (tmp_path / "note.txt").write_text("VALUE-7", encoding="utf-8")
    a = Scripted([_ai(calls=[("read_file", {"path": "note.txt"})]), _ai("文件内容是 VALUE-7", i=20, o=8)])
    r, evs = await _collect(a, _req(tmp_path, input_price_per_mtok=1.0, output_price_per_mtok=2.0))
    assert r.ok and r.output == "文件内容是 VALUE-7"
    assert (r.input_tokens, r.output_tokens) == (30, 13)
    assert r.cost_usd == pytest.approx((30 * 1 + 13 * 2) / 1e6)
    kinds = [(e.kind, e.name) for e in evs]
    assert kinds[0] == ("session.started", "langchain agent")
    assert ("tool.call", "read_file") in kinds and ("tool.result", "read_file") in kinds
    assert sum(k == "model.call" for k, _ in kinds) == 2
    call = next(e for e in evs if e.kind == "tool.call")
    res = next(e for e in evs if e.kind == "tool.result")
    assert call.call_id == res.call_id and "VALUE-7" in res.output


async def test_write_tool_only_when_enabled(tmp_path):
    a = Scripted([_ai(calls=[("write_file", {"path": "x.txt", "content": "y"})]), _ai("done")])
    r, evs = await _collect(a, _req(tmp_path))  # default tools are read-only
    assert not (tmp_path / "x.txt").exists()
    assert risk_warnings("langchain", {"tools": ["read_file"]}) == []
    assert risk_warnings("langchain", {"tools": ["read_file", "write_file"]})
    a = Scripted([_ai(calls=[("write_file", {"path": "x.txt", "content": "y"})]), _ai("done")])
    r, _ = await _collect(a, _req(tmp_path, tools=["write_file"]))
    assert r.ok and (tmp_path / "x.txt").read_text(encoding="utf-8") == "y"


async def test_recursion_limit_and_no_key_and_cancel(tmp_path):
    loop = [_ai(calls=[("list_files", {"path": "."})]) for _ in range(20)]
    r, _ = await _collect(Scripted(loop), _req(tmp_path, max_iterations=2))
    assert not r.ok and "最大推理轮数" in r.error

    req = _req(tmp_path, api_key_env="AGENTOPS_DEFINITELY_UNSET")
    req.secrets = {}
    r, _ = await _collect(LangChainAdapter(), req)
    assert not r.ok and "API Key" in r.error

    cancel = asyncio.Event()
    asyncio.get_running_loop().call_later(0.2, cancel.set)
    r, _ = await _collect(Scripted([_ai("late")], delay=5), _req(tmp_path), cancel)
    assert r.cancelled and not r.ok


async def test_platform_task_with_langchain_agent(client, admin, monkeypatch):
    """Through the API: agent CRUD validation, task run, trace events persisted."""
    scripted = Scripted([_ai(calls=[("list_files", {"path": "."})]), _ai("目录为空")])
    monkeypatch.setitem(ADAPTERS, "langchain", scripted)
    r = await client.post("/api/agents", json={"name": "LC", "adapter_type": "langchain", "model": ""})
    assert r.status_code == 422 and "模型" in r.json()["detail"]
    agent = (await client.post("/api/agents", json={"name": "LC", "adapter_type": "langchain", "model": "deepseek-flash",
                                                     "secrets": {"api_key": "sk-test-1234567890abcdef"}})).json()
    assert agent["secret_keys_set"] == ["api_key"] and agent["config"]["api_key_env"] == "DEEPSEEK_API_KEY"
    tid = (await client.post("/api/tasks", json={"prompt": "看看目录", "agent_ids": [agent["id"]]})).json()["id"]
    for _ in range(200):
        t = (await client.get(f"/api/tasks/{tid}")).json()
        if t["status"] in ("completed", "failed") and not t["active"]:
            break
        await asyncio.sleep(0.05)
    assert t["status"] == "completed" and t["runs"][0]["output_text"] == "目录为空"
    types = [e["event_type"] for e in (await client.get(f"/api/tasks/{tid}/events")).json()]
    assert types.count("model.call") == 2 and "tool.call" in types and "tool.result" in types
