"""Claude Code adapter: `claude -p --output-format stream-json` (verified locally with 2.1.312)."""

import asyncio
import json
import time

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult
from .cli_runner import resolve_executable, run_streaming, run_version


def _summ(obj, limit=600) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s[:limit]


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return _summ(content)


class ClaudeStreamParser:
    """Turns stream-json lines into AdapterEvents and accumulates the final result."""

    def __init__(self):
        self.result = RunResult(ok=False)
        self._seen_messages: set[str] = set()
        self._tool_started: dict[str, float] = {}
        self._texts: list[str] = []
        self.got_result = False

    def feed(self, line: str) -> list[AdapterEvent]:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return [AdapterEvent(kind="log", name="stdout", output=line[:500])]
        t = d.get("type")
        out: list[AdapterEvent] = []
        if t == "system" and d.get("subtype") == "init":
            self.result.session_id = d.get("session_id")
            self.result.model = d.get("model")
            out.append(
                AdapterEvent(
                    kind="session.started",
                    name="claude-code session",
                    output=f"model={d.get('model')} tools={len(d.get('tools') or [])}",
                    attributes={
                        "session_id": d.get("session_id"),
                        "model": d.get("model"),
                        "tools": d.get("tools"),
                        "mcp_servers": d.get("mcp_servers"),
                        "permission_mode": d.get("permissionMode"),
                        "claude_code_version": d.get("claude_code_version"),
                    },
                )
            )
        elif t == "system" and d.get("subtype") == "permission_denied":
            out.append(
                AdapterEvent(kind="permission.denied", name=str(d.get("tool_name") or "tool"), status="denied",
                             input=_summ(d), attributes={"raw_subtype": "permission_denied"})
            )
        elif t == "assistant":
            msg = d.get("message") or {}
            mid = msg.get("id")
            if mid and mid not in self._seen_messages and msg.get("usage"):
                self._seen_messages.add(mid)
                u = msg["usage"]
                out.append(
                    AdapterEvent(
                        kind="model.call",
                        name=msg.get("model") or "model",
                        attributes={
                            "model": msg.get("model"),
                            "input_tokens": u.get("input_tokens"),
                            "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
                            "cache_read_input_tokens": u.get("cache_read_input_tokens"),
                            "output_tokens": u.get("output_tokens"),
                            "note": "output_tokens may be partial for streamed messages; totals come from result",
                        },
                    )
                )
            for c in msg.get("content") or []:
                ct = c.get("type")
                if ct == "text" and c.get("text"):
                    self._texts.append(c["text"])
                    out.append(AdapterEvent(kind="agent.message", name="assistant", output=c["text"]))
                elif ct == "tool_use":
                    name = c.get("name", "tool")
                    attrs = {"tool_use_id": c.get("id")}
                    if name.startswith("mcp__"):
                        parts = name.split("__", 2)
                        if len(parts) == 3:
                            attrs.update(mcp_server=parts[1], mcp_tool=parts[2])
                    self._tool_started[c.get("id")] = time.monotonic()
                    out.append(AdapterEvent(kind="tool.call", name=name, status="started", input=_summ(c.get("input")),
                                            call_id=c.get("id"), attributes=attrs))
        elif t == "user":
            for c in (d.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    tid = c.get("tool_use_id")
                    started = self._tool_started.pop(tid, None)
                    out.append(
                        AdapterEvent(
                            kind="tool.result",
                            name="tool_result",
                            status="error" if c.get("is_error") else "ok",
                            output=_tool_result_text(c.get("content"))[:1500],
                            call_id=tid,
                            duration_ms=int((time.monotonic() - started) * 1000) if started else None,
                        )
                    )
        elif t == "result":
            self.got_result = True
            r = self.result
            usage = d.get("usage") or {}
            r.ok = not d.get("is_error") and d.get("subtype") == "success"
            r.output = d.get("result") or "\n\n".join(self._texts)
            r.session_id = d.get("session_id") or r.session_id
            r.cost_usd = d.get("total_cost_usd")
            r.input_tokens = sum(
                usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            )
            r.output_tokens = usage.get("output_tokens")
            if not r.ok:
                r.error = f"claude result subtype={d.get('subtype')}: {(d.get('result') or '')[:500]}"
            for den in d.get("permission_denials") or []:
                out.append(AdapterEvent(kind="permission.denied", name=den.get("tool_name", "tool"), status="denied",
                                        input=_summ(den.get("tool_input")), call_id=den.get("tool_use_id")))
        return out


class ClaudeCodeAdapter(AgentAdapter):
    type = "claude_code"
    display_name = "Claude Code"
    platform = "Anthropic Claude Code"
    transport = "cli"
    verified = True
    description = "通过 claude -p --output-format stream-json 驱动本机 Claude Code，默认隔离用户级设置/MCP 并仅开放只读工具。"
    config_fields = [
        ConfigField("executable", "可执行文件", default="claude", help="命令名或绝对路径"),
        ConfigField("tools", "可用工具", "string_list",
                    default=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
                    help="--tools。留空则不限制（用 CLI 默认工具集）；Bash/Write/Edit 属于高风险工具，会出现在风险提示里"),
        ConfigField("allowed_tools", "免审批工具", "string_list",
                    default=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
                    help="--allowedTools。非交互模式下不在白名单里的调用会被直接拒绝；要能干活得有 Write/Edit/Bash"),
        ConfigField("permission_mode", "权限模式", type="select", default="acceptEdits",
                    options=["default", "acceptEdits", "plan", "bypassPermissions"],
                    help="--permission-mode。default 在无人值守时等于全部拒绝；acceptEdits 自动接受文件编辑；"
                         "bypassPermissions 全部放行（危险）"),
        ConfigField("isolate", "隔离用户级配置", "bool", default=True,
                    help="--setting-sources project --strict-mcp-config：不加载用户全局插件/MCP/CLAUDE.md，降低成本并避免越权"),
        ConfigField("max_turns", "最大轮数", "number", default=20),
        ConfigField("max_budget_usd", "单次最大花费 (USD)", "number", default=1.0),
        ConfigField("append_system_prompt", "附加系统提示词", "text", default=""),
        ConfigField("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", secret=True,
                    help="可选；留空则使用本机 Claude Code 已登录的凭据"),
    ]
    capabilities = {
        "connection_check": Level.supported,
        "send_task": Level.supported,
        "stream_events": Level.supported,
        "tool_events": Level.supported,
        "mcp_events": Level.supported,
        "token_usage": Level.supported,
        "cost": Level.supported,
        "cancel": Level.supported,
        "permission_control": Level.supported,
        "session_resume": Level.supported,
        "model_select": Level.supported,
        "mcp_config": Level.supported,
    }

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        exe = resolve_executable(config.get("executable") or "claude")
        if not exe:
            return ConnectionResult(False, "未找到 claude 可执行文件，请确认已安装 Claude Code 或填写绝对路径")
        t0 = time.monotonic()
        code, out = await run_version(exe)
        latency = int((time.monotonic() - t0) * 1000)
        if code != 0:
            return ConnectionResult(False, f"claude --version 失败: {out[:300]}", latency_ms=latency)
        version = out.strip().splitlines()[0] if out.strip() else None
        return ConnectionResult(
            True,
            "CLI 可用。认证状态与可用工具在首次真实任务的 init 事件中检测。",
            version=version,
            latency_ms=latency,
            detected={"executable": exe, "version": version},
        )

    def build_argv(self, exe: str, req: RunRequest) -> list[str]:
        c = req.config
        mode = c.get("permission_mode") or "acceptEdits"
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", mode,
                "--no-session-persistence"]
        if c.get("isolate", True):
            argv += ["--setting-sources", "project", "--strict-mcp-config"]
        tools = c.get("tools") or []
        # 留空表示“用 CLI 默认工具集”，绝不能传 --tools ""（那会把内置工具全关掉）
        if tools:
            argv += ["--tools", *tools]
        if c.get("allowed_tools"):
            argv += ["--allowedTools", *c["allowed_tools"]]
        if req.model:
            argv += ["--model", req.model]
        if c.get("max_turns"):
            argv += ["--max-turns", str(int(c["max_turns"]))]
        if c.get("max_budget_usd"):
            argv += ["--max-budget-usd", str(c["max_budget_usd"])]
        if c.get("append_system_prompt"):
            argv += ["--append-system-prompt", c["append_system_prompt"]]
        return argv

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        exe = resolve_executable(req.config.get("executable") or "claude")
        if not exe:
            return RunResult(ok=False, error="未找到 claude 可执行文件")
        parser = ClaudeStreamParser()

        async def on_line(line: str):
            for ev in parser.feed(line):
                await emit(ev)

        env = {k: v for k, v in req.secrets.items() if k == "ANTHROPIC_API_KEY" and v}
        outcome = await run_streaming(self.build_argv(exe, req), cwd=req.workdir, on_line=on_line, cancel=cancel,
                                      timeout_sec=req.timeout_sec, stdin_text=req.prompt, env_extra=env)
        r = parser.result
        r.timed_out, r.cancelled = outcome.timed_out, outcome.cancelled
        if outcome.timed_out:
            r.ok, r.error = False, f"执行超时（{req.timeout_sec}s），已终止进程"
        elif outcome.cancelled:
            r.ok, r.error = False, "已取消，进程已终止"
        elif not parser.got_result:
            r.ok = False
            r.error = f"进程退出码 {outcome.exit_code}，未收到 result 事件。stderr: {outcome.stderr_tail[-800:]}"
        return r
