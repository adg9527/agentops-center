"""Unit tests: secret handling, redaction, and adapter parsers against REAL recorded agent output."""

from pathlib import Path

from conftest import FIXTURES

from app.adapters.claude_code import ClaudeCodeAdapter, ClaudeStreamParser
from app.adapters.base import RunRequest
from app.adapters.opencode import OpenCodeParser
from app.adapters.codex import CodexParser
from app.adapters.http_agent import dig
from app.api.agents import risk_warnings
from app.security import decrypt_secrets, encrypt_secrets, hash_password, redact, verify_password


def test_password_hash():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong", h)


async def test_secret_roundtrip(app_env):
    token = encrypt_secrets({"ANTHROPIC_API_KEY": "sk-ant-1234567890abcdef", "empty": ""})
    assert "sk-ant" not in token
    assert decrypt_secrets(token) == {"ANTHROPIC_API_KEY": "sk-ant-1234567890abcdef"}
    assert decrypt_secrets(None) == {}
    assert decrypt_secrets("garbage") == {}


def test_redact():
    s = redact("key sk-abcdefghijklmnopqrst and Bearer abcdefghijklmnop12 api_key=supersecretvalue x",
               known_secrets=["myLiteralSecret"])
    assert "sk-abcdefghijklmnopqrst" not in s
    assert "abcdefghijklmnop12" not in s
    assert "supersecretvalue" not in s
    assert redact("contains myLiteralSecret here", ["myLiteralSecret"]) == "contains ****** here"
    assert redact("x" * 3000, limit=100).endswith("chars]")


def test_claude_parser_on_real_output():
    p = ClaudeStreamParser()
    events = []
    for line in (FIXTURES / "claude_stream.jsonl").read_text(encoding="utf-8").splitlines():
        events += p.feed(line)
    kinds = [e.kind for e in events]
    assert kinds[0] == "session.started"
    assert "tool.call" in kinds and "tool.result" in kinds and "model.call" in kinds
    assert "permission.denied" in kinds  # Write was blocked by the allowlist
    read_calls = [e for e in events if e.kind == "tool.call" and e.name == "Read"]
    assert read_calls and read_calls[0].call_id
    results = [e for e in events if e.kind == "tool.result" and e.call_id == read_calls[0].call_id]
    assert results and "hello-from-file" in results[0].output
    r = p.result
    assert p.got_result and r.ok
    assert r.session_id and r.cost_usd is not None and r.cost_usd > 0
    assert r.input_tokens and r.output_tokens
    assert "hello-from-file" in r.output


def test_opencode_parser_on_real_output():
    p = OpenCodeParser("opencode/big-pickle")
    events = []
    for line in (FIXTURES / "opencode_events.jsonl").read_text(encoding="utf-8").splitlines():
        events += p.feed(line)
    r = p.finish()
    kinds = [e.kind for e in events]
    assert "session.started" in kinds and "tool.call" in kinds and "tool.result" in kinds
    assert "model.call" in kinds and "agent.message" in kinds
    assert r.ok and r.session_id and r.input_tokens > 0
    assert r.output


def test_codex_parser_documented_events():
    p = CodexParser()
    lines = [
        '{"type":"thread.started","thread_id":"t1"}',
        '{"type":"item.started","item":{"id":"i1","type":"command_execution","command":"ls","status":"in_progress"}}',
        '{"type":"item.completed","item":{"id":"i1","type":"command_execution","command":"ls","aggregated_output":"a.txt","exit_code":0}}',
        '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"done"}}',
        '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":5,"output_tokens":3}}',
    ]
    events = [e for ln in lines for e in p.feed(ln)]
    assert [e.kind for e in events] == ["session.started", "tool.call", "tool.result", "agent.message", "model.call"]
    assert p.result.input_tokens == 15 and p.got_turn_end


def test_claude_argv_is_least_privilege(tmp_path):
    a = ClaudeCodeAdapter()
    req = RunRequest(prompt="hi", workdir=tmp_path, config=a.defaults(), secrets={}, model="haiku", timeout_sec=60)
    argv = a.build_argv("claude", req)
    assert "hi" not in argv  # prompt goes via stdin, never argv
    assert argv[argv.index("--tools") + 1: argv.index("--allowedTools")] == ["Read", "Glob", "Grep"]
    assert "--strict-mcp-config" in argv and "--setting-sources" in argv
    assert risk_warnings("claude_code", a.defaults()) == []
    assert risk_warnings("claude_code", {**a.defaults(), "tools": ["Bash"]})
    assert risk_warnings("opencode", {"permission": {"bash": "allow"}})


def test_dig():
    assert dig({"a": {"b": [{"c": "x"}]}}, "a.b.0.c") == "x"
    assert dig({"a": 1}, "a.b") is None


# --- regressions for environment issues found during phase-1 verification ---

async def test_opencode_pins_workspace_dir(tmp_path, monkeypatch):
    """opencode resolves its project from $PWD, not cwd; it once read the backend dir instead of the workspace."""
    from app.adapters import opencode as oc

    captured = {}

    async def fake_stream(argv, **kw):
        captured["argv"], captured["kw"] = argv, kw
        from app.adapters.cli_runner import CliOutcome

        return CliOutcome(0, "", False, False, 1)

    monkeypatch.setattr(oc, "resolve_opencode", lambda _: "opencode.exe")
    monkeypatch.setattr(oc, "run_streaming", fake_stream)
    a = oc.OpenCodeAdapter()
    req = RunRequest(prompt="hi", workdir=tmp_path, config=a.defaults(), secrets={}, model="opencode/m", timeout_sec=5)
    await a.run(req, lambda e: None, None)
    argv = captured["argv"]
    assert argv[argv.index("--dir") + 1] == str(tmp_path)
    assert captured["kw"]["cwd"] == tmp_path


def test_http_adapter_bypasses_proxy_for_loopback():
    from app.adapters.http_agent import _client

    assert _client("http://127.0.0.1:9000/run", 5)._trust_env is False
    assert _client("http://localhost:9000/run", 5)._trust_env is False
    assert _client("https://agents.example.com/run", 5)._trust_env is True


def test_spa_shell_rendered_from_manifest():
    from app.main import _render_shell

    html = _render_shell({"index.html": {"file": "assets/index-abc.js", "css": ["assets/x.css"], "isEntry": True}})
    assert '<script type="module" crossorigin src="/assets/index-abc.js">' in html
    assert '<link rel="stylesheet" href="/assets/x.css">' in html


async def test_opencode_bare_model_name_is_completed_or_rejected(monkeypatch):
    """A bare model name is completed to `provider/model`; an unknown one still fails fast."""
    from app.adapters.opencode import OpenCodeAdapter

    a = OpenCodeAdapter()

    async def fake_models(exe, refresh=False):
        return ["opencode/deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash", "opencode/big-pickle"]

    async def emit(_event):
        return None

    monkeypatch.setattr(a, "list_models", fake_models)
    monkeypatch.setattr("app.adapters.opencode.resolve_opencode", lambda _: "opencode.exe")

    # a resolvable bare name is accepted, and normalized to the provider-prefixed id
    assert await a.validate_model("deepseek-v4.1-flash", {}) is None
    assert await a.normalize_model("deepseek-v4.1-flash", {}) == "opencode/deepseek-v4.1-flash"
    assert await a.validate_model("opencode/deepseek-v4.1-flash", {}) is None

    # unknown names still explain the required format instead of failing opaquely at run time
    assert "provider/" in await a.validate_model("no-such-model", {})
    assert "没有模型" in await a.validate_model("opencode/nope", {})
    assert await a.validate_model("", {}) is None

    r = await a.run(RunRequest(prompt="x", workdir=Path("."), config={}, secrets={}, model="no-such-model", timeout_sec=5),
                    emit, None)
    assert not r.ok and "provider/" in r.error  # fails fast with a clear message, no process launched


def test_opencode_empty_output_is_a_failure():
    """只调工具、没有文本时不能算成功，否则聊天里会出现空白气泡。"""
    import json as _json

    from app.adapters.opencode import OpenCodeParser

    parser = OpenCodeParser("opencode/deepseek-v4.1-flash")
    parser.feed(_json.dumps({"type": "step_start", "sessionID": "ses_x", "part": {"type": "step-start"}}))
    parser.feed(_json.dumps({"type": "step_finish", "part": {"type": "step-finish", "tokens": {"input": 3, "output": 1}}}))
    result = parser.finish()
    assert result.ok is False
    assert "没有返回任何文本" in (result.error or "")


def test_opencode_text_output_is_kept():
    import json as _json

    from app.adapters.opencode import OpenCodeParser

    parser = OpenCodeParser("opencode/deepseek-v4.1-flash")
    parser.feed(_json.dumps({"type": "step_start", "sessionID": "ses_y", "part": {"type": "step-start"}}))
    parser.feed(_json.dumps({"type": "text", "part": {"type": "text", "text": "你好"}}))
    result = parser.finish()
    assert result.ok is True
    assert result.output == "你好"
