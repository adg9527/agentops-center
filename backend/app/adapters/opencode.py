"""OpenCode adapter: `opencode run --format json` (verified locally with 1.19.5)."""

import asyncio
import json
import time
from pathlib import Path

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult
from .cli_runner import resolve_executable, run_streaming, run_version

DEFAULT_PERMISSION = {"edit": "deny", "bash": "deny", "webfetch": "deny"}
# Windows CreateProcess command line limit is 32767 chars; keep headroom for other args.
_MAX_ARGV_PROMPT = 24000


def resolve_opencode(configured: str | None) -> str | None:
    """Prefer the native opencode.exe over npm's .cmd/.ps1 shim so arguments never pass through cmd.exe."""
    exe = resolve_executable(configured or "opencode")
    if not exe:
        return None
    p = Path(exe)
    if p.suffix.lower() in ("", ".cmd", ".ps1", ".bat"):
        native = p.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if native.is_file():
            return str(native)
        if p.suffix.lower() in (".cmd", ".bat"):
            return None  # refuse to route prompts through cmd.exe
    return exe


def _summ(obj, limit=600) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return (s or "")[:limit]


class OpenCodeParser:
    def __init__(self, model: str):
        self.result = RunResult(ok=True, model=model or None)
        self._texts: list[str] = []
        self.in_tokens = 0
        self.out_tokens = 0
        self.cost = 0.0
        self.saw_step = False

    def feed(self, line: str) -> list[AdapterEvent]:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return [AdapterEvent(kind="log", name="stdout", output=line[:500])]
        t = d.get("type")
        part = d.get("part") or {}
        out: list[AdapterEvent] = []
        if d.get("sessionID") and not self.result.session_id:
            self.result.session_id = d["sessionID"]
            out.append(AdapterEvent(kind="session.started", name="opencode session",
                                    attributes={"session_id": d["sessionID"], "model": self.result.model}))
        if t == "step_start":
            self.saw_step = True
        elif t == "text" and part.get("text"):
            self._texts.append(part["text"])
            out.append(AdapterEvent(kind="agent.message", name="assistant", output=part["text"]))
        elif t == "tool_use":
            state = part.get("state") or {}
            tool = part.get("tool", "tool")
            call_id = part.get("callID") or part.get("id")
            tm = state.get("time") or {}
            dur = (tm["end"] - tm["start"]) if tm.get("start") and tm.get("end") else None
            status = state.get("status")
            out.append(AdapterEvent(kind="tool.call", name=tool, status="started", input=_summ(state.get("input")),
                                    call_id=call_id, attributes={"title": state.get("title")}))
            if status in ("completed", "error"):
                out.append(AdapterEvent(kind="tool.result", name=tool, status="ok" if status == "completed" else "error",
                                        output=_summ(state.get("output") or state.get("error"), 1500),
                                        call_id=call_id, duration_ms=dur))
        elif t == "step_finish":
            tok = part.get("tokens") or {}
            cache = tok.get("cache") or {}
            in_t = (tok.get("input") or 0) + (cache.get("read") or 0) + (cache.get("write") or 0)
            out_t = (tok.get("output") or 0) + (tok.get("reasoning") or 0)
            self.in_tokens += in_t
            self.out_tokens += out_t
            self.cost += part.get("cost") or 0
            out.append(AdapterEvent(kind="model.call", name=self.result.model or "model",
                                    attributes={"input_tokens": in_t, "output_tokens": out_t, "cost": part.get("cost"),
                                                "finish_reason": part.get("reason"), "raw_tokens": tok}))
        elif t == "error":
            err = d.get("error") or part.get("error") or d
            self.result.ok = False
            self.result.error = _summ(err, 1000)
            out.append(AdapterEvent(kind="agent.error", name="opencode error", status="error", output=_summ(err, 1000)))
        return out

    def finish(self) -> RunResult:
        r = self.result
        r.output = "\n\n".join(self._texts)
        r.input_tokens, r.output_tokens, r.cost_usd = self.in_tokens, self.out_tokens, self.cost
        return r


class OpenCodeAdapter(AgentAdapter):
    type = "opencode"
    display_name = "OpenCode"
    platform = "OpenCode (sst/opencode)"
    transport = "cli"
    verified = True
    description = "通过 opencode run --format json 驱动本机 OpenCode；权限通过 OPENCODE_CONFIG_CONTENT 注入（默认禁止编辑/命令/联网）。"
    config_fields = [
        ConfigField("executable", "可执行文件", default="opencode", help="自动解析为原生 opencode.exe，避免经由 cmd.exe"),
        ConfigField("agent", "OpenCode Agent", default="", help="--agent，可留空"),
        ConfigField("permission", "权限配置", "json", default=DEFAULT_PERMISSION,
                    help='OpenCode permission 配置，例如 {"edit":"deny","bash":"deny"}；"allow" 属于高风险'),
        ConfigField("pure", "禁用外部插件", "bool", default=False, help="--pure"),
        ConfigField("OPENCODE_API_KEY", "Provider API Key (可选)", secret=True,
                    help="一般留空，使用 opencode providers 已配置的凭据"),
    ]
    capabilities = {
        "connection_check": Level.supported,
        "send_task": Level.supported,
        "stream_events": Level.supported,
        "tool_events": Level.supported,
        "mcp_events": Level.partial,  # MCP tools appear as `<server>_<tool>`; not reliably distinguishable
        "token_usage": Level.supported,
        "cost": Level.supported,
        "cancel": Level.supported,
        "permission_control": Level.supported,
        "session_resume": Level.supported,
        "model_select": Level.supported,
        "mcp_config": Level.supported,
    }

    _models_cache: tuple[float, str, list[str]] | None = None

    async def list_models(self, exe: str, refresh: bool = False) -> list[str]:
        """`opencode models` output (provider/model per line), cached for 5 minutes."""
        c = self._models_cache
        if not refresh and c and c[1] == exe and time.monotonic() - c[0] < 300:
            return c[2]
        code, out = await run_version(exe, ["models"], timeout=60)
        models = [ln.strip() for ln in out.splitlines() if "/" in ln and " " not in ln.strip()] if code == 0 else []
        if models:
            self._models_cache = (time.monotonic(), exe, models)
        return models

    async def validate_model(self, model: str, config: dict) -> str | None:
        # opencode needs "provider/model"; a bare name fails at run time with an opaque
        # {"name":"UnknownError","data":{"message":"Unexpected server error..."}}.
        if not model:
            return None
        exe = resolve_opencode(config.get("executable"))
        models = await self.list_models(exe) if exe else []
        if "/" not in model:
            candidates = [m for m in models if m.split("/", 1)[1] == model]
            hint = f"，可选：{'、'.join(candidates)}" if candidates else "（可点「测试连接」后在下拉框中选择）"
            return f"OpenCode 模型必须写成「provider/模型名」格式，例如 opencode/big-pickle{hint}"
        if models and model not in models:
            return f"OpenCode 中没有模型「{model}」。请检查拼写，或运行 opencode models 查看可用模型"
        return None

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        exe = resolve_opencode(config.get("executable"))
        if not exe:
            return ConnectionResult(False, "未找到 opencode 原生可执行文件（opencode.exe）")
        t0 = time.monotonic()
        code, out = await run_version(exe)
        latency = int((time.monotonic() - t0) * 1000)
        if code != 0:
            return ConnectionResult(False, f"opencode --version 失败: {out[:300]}", latency_ms=latency)
        version = out.strip().splitlines()[-1] if out.strip() else None
        models = await self.list_models(exe, refresh=True)
        return ConnectionResult(
            True,
            f"CLI 可用，检测到 {len(models)} 个可用模型" if models else "CLI 可用（模型列表获取失败）",
            version=version,
            latency_ms=latency,
            detected={"executable": exe, "version": version, "models": models},
        )

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        exe = resolve_opencode(req.config.get("executable"))
        if not exe:
            return RunResult(ok=False, error="未找到 opencode 原生可执行文件")
        if err := await self.validate_model(req.model, req.config):
            return RunResult(ok=False, error=err)
        c = req.config
        # --dir is required: opencode resolves its project from $PWD rather than the process cwd.
        argv = [exe, "run", "--format", "json", "--dir", str(req.workdir)]
        if req.model:
            argv += ["-m", req.model]
        if c.get("agent"):
            argv += ["--agent", c["agent"]]
        if c.get("pure"):
            argv.append("--pure")
        if len(req.prompt) > _MAX_ARGV_PROMPT:
            prompt_file = req.workdir / "_task_prompt.md"
            prompt_file.write_text(req.prompt, encoding="utf-8")
            argv += ["-f", str(prompt_file), "--", "完整任务说明见附件 _task_prompt.md，请按其要求执行。"]
        else:
            argv += ["--", req.prompt]
        env = {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": c.get("permission") or DEFAULT_PERMISSION})}
        parser = OpenCodeParser(req.model)

        async def on_line(line: str):
            for ev in parser.feed(line):
                await emit(ev)

        outcome = await run_streaming(argv, cwd=req.workdir, on_line=on_line, cancel=cancel,
                                      timeout_sec=req.timeout_sec, env_extra=env)
        r = parser.finish()
        r.timed_out, r.cancelled = outcome.timed_out, outcome.cancelled
        if outcome.timed_out:
            r.ok, r.error = False, f"执行超时（{req.timeout_sec}s），已终止进程"
        elif outcome.cancelled:
            r.ok, r.error = False, "已取消，进程已终止"
        elif outcome.exit_code not in (0, None) and r.ok:
            r.ok, r.error = False, f"进程退出码 {outcome.exit_code}。stderr: {outcome.stderr_tail[-800:]}"
        elif not parser.saw_step and r.ok:
            r.ok, r.error = False, f"未收到任何执行事件。stderr: {outcome.stderr_tail[-800:]}"
        return r
