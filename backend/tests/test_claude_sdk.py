"""Claude Agent SDK adapter: typed messages in, platform events out."""

import asyncio

import pytest

from app.adapters import claude_sdk as mod
from app.adapters.base import RunRequest


class FakeText:
    def __init__(self, text):
        self.text = text


class FakeToolUse:
    def __init__(self, id_, name, input_):
        self.id = id_
        self.name = name
        self.input = input_


class FakeToolResult:
    def __init__(self, tool_use_id, content, is_error=False):
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class FakeAssistant:
    def __init__(self, content, model="claude-sonnet", usage=None, error=None):
        self.content = content
        self.model = model
        self.usage = usage
        self.error = error


class FakeUser:
    def __init__(self, content):
        self.content = content


class FakeSystem:
    def __init__(self, subtype, data):
        self.subtype = subtype
        self.data = data


class FakeResult:
    def __init__(self, result="done", is_error=False, total_cost_usd=0.01, usage=None, session_id="s1", errors=None):
        self.result = result
        self.is_error = is_error
        self.total_cost_usd = total_cost_usd
        self.usage = usage
        self.session_id = session_id
        self.errors = errors
        self.subtype = "success"


@pytest.fixture
def adapter(monkeypatch):
    a = mod.ClaudeSdkAdapter()
    monkeypatch.setattr(mod, "resolve_executable", lambda name: "C:/fake/claude.exe")
    monkeypatch.setattr(mod, "AssistantMessage", FakeAssistant)
    monkeypatch.setattr(mod, "UserMessage", FakeUser)
    monkeypatch.setattr(mod, "SystemMessage", FakeSystem)
    monkeypatch.setattr(mod, "ResultMessage", FakeResult)
    monkeypatch.setattr(mod, "TextBlock", FakeText)
    monkeypatch.setattr(mod, "ToolUseBlock", FakeToolUse)
    monkeypatch.setattr(mod, "ToolResultBlock", FakeToolResult)
    monkeypatch.setattr(mod, "SDK_IMPORT_ERROR", None)
    return a


def _request(model="claude-sonnet", config=None, timeout_sec=30):
    return RunRequest(prompt="hi", workdir=__import__("pathlib").Path("."), config=config or {},
                      secrets={}, model=model, timeout_sec=timeout_sec)


def _install_query(monkeypatch, factory):
    monkeypatch.setattr(mod, "query", factory)


async def test_run_maps_messages_to_events_and_result(adapter, monkeypatch):
    async def gen(*, prompt, options):
        assert prompt == "hi"
        assert options.cwd
        yield FakeSystem("init", {"session_id": "s1", "model": "claude-sonnet",
                                  "tools": ["Read"], "permissionMode": "acceptEdits"})
        yield FakeAssistant([FakeText("你好"), FakeToolUse("t1", "Read", {"path": "a.txt"})],
                            usage={"input_tokens": 5, "output_tokens": 7})
        yield FakeUser([FakeToolResult("t1", "ok")])
        yield FakeResult(result="完成", total_cost_usd=0.0123, usage={"input_tokens": 5, "output_tokens": 7})

    _install_query(monkeypatch, gen)
    events = []

    async def emit(ev):
        events.append(ev)

    result = await adapter.run(_request(), emit, asyncio.Event())

    kinds = [e.kind for e in events]
    assert kinds[0] == "session.started"
    assert "agent.message" in kinds
    assert "tool.call" in kinds and "tool.result" in kinds
    assert events[kinds.index("tool.call")].call_id == "t1"
    assert result.ok and result.output == "完成"
    assert result.session_id == "s1"
    assert result.cost_usd == pytest.approx(0.0123)
    assert (result.input_tokens, result.output_tokens) == (5, 7)


async def test_run_reports_error_result(adapter, monkeypatch):
    async def gen(*, prompt, options):
        yield FakeResult(result="boom", is_error=True)

    _install_query(monkeypatch, gen)
    result = await adapter.run(_request(), lambda ev: asyncio.sleep(0), asyncio.Event())

    assert not result.ok
    assert result.error and "boom" in result.error


async def test_run_can_be_cancelled(adapter, monkeypatch):
    started = asyncio.Event()

    async def gen(*, prompt, options):
        yield FakeSystem("init", {"session_id": "s1", "model": "m", "tools": []})
        started.set()
        await asyncio.sleep(30)  # a long turn
        yield FakeResult()

    _install_query(monkeypatch, gen)
    cancel = asyncio.Event()
    task = asyncio.create_task(adapter.run(_request(timeout_sec=60), lambda ev: asyncio.sleep(0), cancel))
    await started.wait()
    cancel.set()
    result = await asyncio.wait_for(task, timeout=10)

    assert result.cancelled
    assert not result.ok


async def test_missing_sdk_reports_install_hint(adapter, monkeypatch):
    monkeypatch.setattr(mod, "SDK_IMPORT_ERROR", "ModuleNotFoundError: claude_agent_sdk")
    result = await adapter.run(_request(), lambda ev: asyncio.sleep(0), asyncio.Event())
    assert not result.ok and "claude-agent-sdk" in result.error


def test_adapter_is_registered():
    from app.adapters import ADAPTERS

    assert ADAPTERS["claude_sdk"].display_name.startswith("Claude Code")
