"""Codex CLI adapter: `codex exec --json`.

NOT VERIFIED in this environment (Codex CLI is not installed). Event mapping follows the official
`codex exec --json` documentation; `verified = False` is surfaced in the UI.
"""

import asyncio
import json
import time

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult
from .cli_runner import resolve_executable, run_streaming, run_version


def _summ(obj, limit=600) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return (s or "")[:limit]


class CodexParser:
    def __init__(self):
        self.result = RunResult(ok=True)
        self._texts: list[str] = []
        self.got_turn_end = False

    def feed(self, line: str) -> list[AdapterEvent]:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return [AdapterEvent(kind="log", name="stdout", output=line[:500])]
        t = d.get("type")
        out: list[AdapterEvent] = []
        if t == "thread.started":
            self.result.session_id = d.get("thread_id")
            out.append(AdapterEvent(kind="session.started", name="codex thread", attributes={"thread_id": d.get("thread_id")}))
        elif t in ("item.started", "item.completed"):
            item = d.get("item") or {}
            it = item.get("type")
            done = t == "item.completed"
            if it == "agent_message" and done:
                self._texts.append(item.get("text", ""))
                out.append(AdapterEvent(kind="agent.message", name="assistant", output=item.get("text")))
            elif it == "command_execution":
                kind = "tool.result" if done else "tool.call"
                out.append(AdapterEvent(kind=kind, name="shell", input=_summ(item.get("command")),
                                        output=_summ(item.get("aggregated_output"), 1500) if done else None,
                                        status=("ok" if item.get("exit_code") == 0 else "error") if done else "started",
                                        call_id=item.get("id")))
            elif it == "mcp_tool_call":
                kind = "tool.result" if done else "tool.call"
                out.append(AdapterEvent(kind=kind, name=f"mcp__{item.get('server')}__{item.get('tool')}",
                                        status=(item.get("status") or "ok") if done else "started",
                                        call_id=item.get("id"),
                                        attributes={"mcp_server": item.get("server"), "mcp_tool": item.get("tool")}))
            elif it == "file_change" and done:
                out.append(AdapterEvent(kind="file.change", name="file_change", output=_summ(item.get("changes")),
                                        call_id=item.get("id")))
        elif t == "turn.completed":
            self.got_turn_end = True
            u = d.get("usage") or {}
            self.result.input_tokens = (u.get("input_tokens") or 0) + (u.get("cached_input_tokens") or 0)
            self.result.output_tokens = u.get("output_tokens")
            out.append(AdapterEvent(kind="model.call", name="codex", attributes=u))
        elif t in ("turn.failed", "error"):
            self.got_turn_end = t == "turn.failed"
            self.result.ok = False
            self.result.error = _summ(d.get("error") or d.get("message") or d, 1000)
            out.append(AdapterEvent(kind="agent.error", name=t, status="error", output=self.result.error))
        return out


class CodexAdapter(AgentAdapter):
    type = "codex"
    display_name = "Codex CLI"
    platform = "OpenAI Codex"
    transport = "cli"
    verified = False
    description = "通过 codex exec --json 驱动 Codex CLI（本环境未安装，未经实测验证）。默认 read-only 沙箱。"
    config_fields = [
        ConfigField("executable", "可执行文件", default="codex"),
        ConfigField("sandbox", "沙箱模式", "select", default="read-only", options=["read-only", "workspace-write"],
                    help="workspace-write 允许修改工作目录文件，属于高风险"),
        ConfigField("CODEX_API_KEY", "CODEX_API_KEY", secret=True, help="可选；留空使用 codex login 凭据"),
    ]
    capabilities = {
        "connection_check": Level.supported,
        "send_task": Level.unknown,
        "stream_events": Level.unknown,
        "tool_events": Level.unknown,
        "mcp_events": Level.unknown,
        "token_usage": Level.unknown,
        "cost": Level.unsupported,
        "cancel": Level.supported,
        "permission_control": Level.supported,
        "session_resume": Level.unknown,
        "model_select": Level.unknown,
        "mcp_config": Level.unknown,
    }

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        exe = resolve_executable(config.get("executable") or "codex")
        if not exe:
            return ConnectionResult(False, "未安装 Codex CLI（未找到 codex 命令）。可通过 npm i -g @openai/codex 安装后重试。")
        t0 = time.monotonic()
        code, out = await run_version(exe)
        latency = int((time.monotonic() - t0) * 1000)
        if code != 0:
            return ConnectionResult(False, f"codex --version 失败: {out[:300]}", latency_ms=latency)
        version = out.strip().splitlines()[-1]
        return ConnectionResult(True, "CLI 可用（事件解析未经本环境验证）", version=version, latency_ms=latency,
                                detected={"executable": exe, "version": version})

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        exe = resolve_executable(req.config.get("executable") or "codex")
        if not exe:
            return RunResult(ok=False, error="未安装 Codex CLI")
        argv = [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", req.config.get("sandbox") or "read-only",
                "-C", str(req.workdir)]
        if req.model:
            argv += ["-m", req.model]
        argv.append("-")  # prompt from stdin
        parser = CodexParser()

        async def on_line(line: str):
            for ev in parser.feed(line):
                await emit(ev)

        env = {"CODEX_API_KEY": req.secrets["CODEX_API_KEY"]} if req.secrets.get("CODEX_API_KEY") else {}
        outcome = await run_streaming(argv, cwd=req.workdir, on_line=on_line, cancel=cancel,
                                      timeout_sec=req.timeout_sec, stdin_text=req.prompt, env_extra=env)
        r = parser.result
        r.output = "\n\n".join(parser._texts)
        r.timed_out, r.cancelled = outcome.timed_out, outcome.cancelled
        if outcome.timed_out:
            r.ok, r.error = False, f"执行超时（{req.timeout_sec}s）"
        elif outcome.cancelled:
            r.ok, r.error = False, "已取消"
        elif r.ok and (outcome.exit_code != 0 or not parser.got_turn_end):
            r.ok, r.error = False, f"退出码 {outcome.exit_code}。stderr: {outcome.stderr_tail[-800:]}"
        return r
