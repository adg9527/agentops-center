"""Claude Code adapter over the official Claude Agent SDK (`claude-agent-sdk`).

Unlike the CLI adapter (`claude -p --output-format stream-json`), this drives the
same engine through its supported Python API: typed messages instead of a JSONL
stream, structured tool events, cost/usage on the result message, and hooks or
permission callbacks when a deployment needs them.

Docs: https://code.claude.com/docs/en/agent-sdk/python
"""

import asyncio
import json
import time

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult
from .cli_runner import resolve_executable, run_version

try:  # the package is optional: importing this module must never break the app
    from claude_agent_sdk import (  # type: ignore
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
        query,
    )

    SDK_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - depends on the installed environment
    SDK_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    AssistantMessage = ClaudeAgentOptions = ResultMessage = SystemMessage = None  # type: ignore
    TextBlock = ThinkingBlock = ToolResultBlock = ToolUseBlock = UserMessage = None  # type: ignore
    query = None  # type: ignore

PERMISSION_MODES = ["default", "acceptEdits", "plan", "bypassPermissions"]


def _summ(value, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return _summ(content)


class ClaudeSdkAdapter(AgentAdapter):
    type = "claude_sdk"
    display_name = "Claude Code（Agent SDK）"
    platform = "Claude Code"
    transport = "sdk"
    verified = True
    description = (
        "通过官方 Claude Agent SDK（claude-agent-sdk）直接调用 Claude Code 引擎："
        "类型化消息、工具调用事件、成本与 Token 一次返回。与 CLI 适配器能力相同，但事件更完整、无需解析 stdout。"
    )
    config_fields = [
        ConfigField("cli_path", "claude 可执行文件", help="留空则使用 PATH 中的 claude"),
        ConfigField("permission_mode", "权限模式", type="select", default="acceptEdits", options=PERMISSION_MODES,
                    help="default 需人工确认（无人值守时工具会被拒绝）；acceptEdits 自动接受文件编辑"),
        ConfigField("allowed_tools", "允许的工具", type="string_list", default=[],
                    help="留空表示使用 CLI 默认工具集；填写后只放行这些工具，例如 Bash、Read、Edit"),
        ConfigField("system_prompt", "追加系统提示词", type="text", default=""),
        ConfigField("max_turns", "最大轮次", type="number", default=20),
        ConfigField("max_budget_usd", "预算上限（USD）", type="number", default=0, help="0 表示不限制"),
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
        "mcp_config": Level.partial,
    }

    @staticmethod
    def _options(req: RunRequest, exe: str | None):
        c = req.config
        kwargs: dict = {
            "cwd": str(req.workdir),
            "permission_mode": c.get("permission_mode") or "acceptEdits",
            "include_partial_messages": False,
        }
        if exe:
            kwargs["cli_path"] = exe
        if req.model:
            kwargs["model"] = req.model
        if c.get("system_prompt"):
            kwargs["system_prompt"] = c["system_prompt"]
        if c.get("allowed_tools"):
            kwargs["allowed_tools"] = list(c["allowed_tools"])
        if c.get("max_turns"):
            kwargs["max_turns"] = int(c["max_turns"])
        if c.get("max_budget_usd"):
            kwargs["max_budget_usd"] = float(c["max_budget_usd"])
        api_key = req.secrets.get("ANTHROPIC_API_KEY")
        if api_key:
            kwargs["env"] = {"ANTHROPIC_API_KEY": api_key}
        return ClaudeAgentOptions(**kwargs)

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        if SDK_IMPORT_ERROR:
            return ConnectionResult(False, f"未安装 claude-agent-sdk：{SDK_IMPORT_ERROR}")
        exe = resolve_executable(config.get("cli_path") or "claude")
        if not exe:
            return ConnectionResult(False, "未找到 claude 可执行文件（SDK 仍需要本机安装 Claude Code）")
        t0 = time.monotonic()
        code, out = await run_version(exe)
        latency = int((time.monotonic() - t0) * 1000)
        if code != 0:
            return ConnectionResult(False, f"claude --version 失败: {out[:300]}", latency_ms=latency)
        version = out.strip().splitlines()[0] if out.strip() else None
        return ConnectionResult(
            True,
            "Agent SDK 已就绪；认证状态在首次真实任务的 init 事件中体现。",
            version=version,
            latency_ms=latency,
            detected={"executable": exe, "version": version, "sdk": "claude-agent-sdk"},
        )

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        if SDK_IMPORT_ERROR:
            return RunResult(ok=False, error=f"未安装 claude-agent-sdk：{SDK_IMPORT_ERROR}")
        exe = resolve_executable(req.config.get("cli_path") or "claude")
        if not exe:
            return RunResult(ok=False, error="未找到 claude 可执行文件")

        options = self._options(req, exe)
        state = RunResult(ok=False)
        texts: list[str] = []
        tool_started: dict[str, float] = {}

        stream = query(prompt=req.prompt, options=options)

        async def consume() -> None:
            async for message in stream:
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    data = message.data or {}
                    state.session_id = data.get("session_id")
                    state.model = data.get("model")
                    await emit(AdapterEvent(
                        kind="session.started", name="claude sdk session",
                        output=f"model={state.model} tools={len(data.get('tools') or [])}",
                        attributes={"session_id": state.session_id, "model": state.model,
                                    "permission_mode": data.get("permissionMode"),
                                    "claude_code_version": data.get("claude_code_version")},
                    ))
                elif isinstance(message, AssistantMessage):
                    if message.error:
                        await emit(AdapterEvent(kind="agent.error", name="assistant", status="error",
                                                output=_summ(message.error)))
                    usage = getattr(message, "usage", None) or {}
                    if usage:
                        state.input_tokens = (state.input_tokens or 0) + int(usage.get("input_tokens") or 0)
                        state.output_tokens = (state.output_tokens or 0) + int(usage.get("output_tokens") or 0)
                        await emit(AdapterEvent(
                            kind="model.call", name=message.model or state.model or "model",
                            attributes={"model": message.model, "input_tokens": usage.get("input_tokens"),
                                        "output_tokens": usage.get("output_tokens")},
                        ))
                    for block in message.content or []:
                        if isinstance(block, TextBlock):
                            texts.append(block.text)
                            await emit(AdapterEvent(kind="agent.message", name="assistant", output=block.text))
                        elif isinstance(block, ThinkingBlock):
                            continue
                        elif isinstance(block, ToolUseBlock):
                            tool_started[block.id] = time.monotonic()
                            await emit(AdapterEvent(kind="tool.call", name=block.name, call_id=block.id,
                                                    input=_summ(block.input)))
                elif isinstance(message, UserMessage):
                    for block in message.content or []:
                        if isinstance(block, ToolResultBlock):
                            started = tool_started.pop(block.tool_use_id, None)
                            duration = int((time.monotonic() - started) * 1000) if started else None
                            await emit(AdapterEvent(
                                kind="tool.result", name="tool", call_id=block.tool_use_id,
                                status="error" if block.is_error else "ok",
                                output=_summ(_block_text(block.content)), duration_ms=duration,
                            ))
                elif isinstance(message, ResultMessage):
                    state.session_id = message.session_id or state.session_id
                    state.cost_usd = message.total_cost_usd
                    usage = message.usage or {}
                    if usage:
                        state.input_tokens = int(usage.get("input_tokens") or state.input_tokens or 0)
                        state.output_tokens = int(usage.get("output_tokens") or state.output_tokens or 0)
                    state.ok = not message.is_error
                    state.output = message.result or "\n".join(texts)
                    if message.is_error:
                        state.error = _summ(message.result or message.errors or message.subtype, 800)

        job = asyncio.create_task(consume())
        waiter = asyncio.create_task(cancel.wait())
        done, _ = await asyncio.wait({job, waiter}, timeout=req.timeout_sec, return_when=asyncio.FIRST_COMPLETED)
        timed_out = job not in done and waiter not in done
        cancelled = waiter in done
        waiter.cancel()
        if job not in done:
            job.cancel()
        try:
            await stream.aclose()  # type: ignore[union-attr]
        except Exception:
            pass
        if job.done() and not job.cancelled():
            exc = job.exception()
            if exc is not None:
                state.ok = False
                state.error = f"{type(exc).__name__}: {exc}"[:800]

        state.cancelled = cancelled
        state.timed_out = timed_out
        if timed_out:
            state.ok, state.error = False, f"执行超时（{req.timeout_sec}s），已终止"
        elif cancelled:
            state.ok, state.error = False, "已取消"
        elif not state.ok and not state.error:
            state.error = "Claude Agent SDK 未返回 result 事件"
        if not state.output:
            state.output = "\n".join(texts)
        return state
