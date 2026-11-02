"""Platform-native agent built with LangChain (`langchain.agents.create_agent`).

Unlike the CLI adapters, the platform owns this agent's loop, so observability is complete: every
model call (tokens, latency) and tool call is reported through LangChain callbacks. Tools are
confined to the task workspace; there is deliberately no shell tool.
"""

import asyncio
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx
import langchain
from langchain.agents import create_agent
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, ToolException

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult

READ_TOOLS = ["list_files", "read_file", "search_text"]
WRITE_TOOLS = ["write_file"]
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS
_MAX_READ = 20_000


def _is_loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("localhost", "::1") or host.startswith("127.")


# ------------------------------------------------------------------ workspace tools


class WorkspaceError(ToolException):
    """Raised for paths outside the workspace; handle_tool_error returns it to the model instead of aborting."""


def _resolve(workdir: Path, rel: str) -> Path:
    root = workdir.resolve()
    p = (root / (rel or ".")).resolve()
    if p != root and root not in p.parents:
        raise WorkspaceError(f"路径越界：{rel!r} 不在任务工作目录内")
    return p


def build_tools(workdir: Path, enabled: list[str]) -> list[StructuredTool]:
    """Tools operate only inside `workdir`; anything resolving outside it is refused."""

    def list_files(path: str = ".") -> str:
        """List files and folders under a path in the task workspace (relative path)."""
        base = _resolve(workdir, path)
        if not base.exists():
            return f"不存在：{path}"
        items = []
        for p in sorted(base.rglob("*"))[:300]:
            rel = p.relative_to(workdir.resolve()).as_posix()
            items.append(rel + ("/" if p.is_dir() else f"  ({p.stat().st_size} bytes)"))
        return "\n".join(items) or "（空目录）"

    def read_file(path: str) -> str:
        """Read a UTF-8 text file from the task workspace (relative path)."""
        p = _resolve(workdir, path)
        if not p.is_file():
            return f"文件不存在：{path}"
        text = p.read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= _MAX_READ else text[:_MAX_READ] + f"\n…[截断，共 {len(text)} 字符]"

    def search_text(pattern: str, path: str = ".") -> str:
        """Search files in the task workspace for a regular expression; returns file:line: text matches."""
        base = _resolve(workdir, path)
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits = []
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for f in files:
            try:
                for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f.relative_to(workdir.resolve()).as_posix()}:{n}: {line[:200]}")
                        if len(hits) >= 100:
                            return "\n".join(hits) + "\n…（仅显示前 100 条）"
            except OSError:
                continue
        return "\n".join(hits) or "无匹配"

    def write_file(path: str, content: str) -> str:
        """Create or overwrite a UTF-8 text file in the task workspace (relative path)."""
        p = _resolve(workdir, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"

    funcs = {"list_files": list_files, "read_file": read_file, "search_text": search_text, "write_file": write_file}
    tools = []
    for name in enabled:
        fn = funcs.get(name)
        if fn:
            tools.append(StructuredTool.from_function(fn, name=name, description=fn.__doc__, handle_tool_error=True))
    return tools


# ------------------------------------------------------------------ callbacks -> trace events


def _usage(msg) -> tuple[int | None, int | None]:
    u = getattr(msg, "usage_metadata", None) or {}
    return u.get("input_tokens"), u.get("output_tokens")


class TraceCallback(AsyncCallbackHandler):
    """Maps LangChain run callbacks onto the platform's normalized events."""

    def __init__(self, emit: Emit, model: str):
        self.emit = emit
        self.model = model
        self._t0: dict[UUID, float] = {}
        self._tool_name: dict[UUID, str] = {}
        self.input_tokens = 0
        self.output_tokens = 0
        self.model_calls = 0

    async def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kw):
        self._t0[run_id] = time.monotonic()

    async def on_llm_end(self, response, *, run_id: UUID, **kw):
        dur = int((time.monotonic() - self._t0.pop(run_id, time.monotonic())) * 1000)
        msg = response.generations[0][0].message if response.generations and response.generations[0] else None
        i, o = _usage(msg) if msg is not None else (None, None)
        self.input_tokens += i or 0
        self.output_tokens += o or 0
        self.model_calls += 1
        tool_calls = [tc["name"] for tc in getattr(msg, "tool_calls", None) or []]
        await self.emit(AdapterEvent(kind="model.call", name=self.model, duration_ms=dur,
                                     attributes={"model": self.model, "input_tokens": i, "output_tokens": o,
                                                 "tool_calls_requested": tool_calls}))
        text = msg.content if isinstance(getattr(msg, "content", None), str) else ""
        if text.strip():
            await self.emit(AdapterEvent(kind="agent.message", name="assistant", output=text))

    async def on_llm_error(self, error, *, run_id: UUID, **kw):
        self._t0.pop(run_id, None)
        await self.emit(AdapterEvent(kind="model.call", name=self.model, status="error", output=f"{type(error).__name__}: {error}"))

    async def on_tool_start(self, serialized, input_str, *, run_id: UUID, **kw):
        name = (serialized or {}).get("name") or kw.get("name") or "tool"
        self._t0[run_id] = time.monotonic()
        self._tool_name[run_id] = name
        await self.emit(AdapterEvent(kind="tool.call", name=name, status="started", input=str(input_str)[:1000], call_id=str(run_id)))

    async def on_tool_end(self, output, *, run_id: UUID, **kw):
        dur = int((time.monotonic() - self._t0.pop(run_id, time.monotonic())) * 1000)
        text = getattr(output, "content", output)
        status = "error" if getattr(output, "status", "") == "error" else "ok"
        await self.emit(AdapterEvent(kind="tool.result", name=self._tool_name.pop(run_id, "tool"), status=status,
                                     output=str(text)[:1500], call_id=str(run_id), duration_ms=dur))

    async def on_tool_error(self, error, *, run_id: UUID, **kw):
        dur = int((time.monotonic() - self._t0.pop(run_id, time.monotonic())) * 1000)
        await self.emit(AdapterEvent(kind="tool.result", name=self._tool_name.pop(run_id, "tool"), status="error",
                                     output=f"{type(error).__name__}: {error}", call_id=str(run_id), duration_ms=dur))


# ------------------------------------------------------------------ adapter


class LangChainAdapter(AgentAdapter):
    type = "langchain"
    display_name = "LangChain Agent（平台内置）"
    platform = "LangChain (platform-native)"
    transport = "sdk"
    verified = True
    description = ("平台用 LangChain create_agent 构建并运行的智能体。支持任意 OpenAI 兼容接口（DeepSeek、通义、Kimi、vLLM、Ollama 等）"
                   "与 Anthropic；工具仅限任务工作目录内的文件操作，不提供命令执行。")
    config_fields = [
        ConfigField("provider", "模型提供方", "select", default="openai_compatible", options=["openai_compatible", "anthropic"]),
        ConfigField("base_url", "API 地址", default="https://api.deepseek.com",
                    help="OpenAI 兼容接口地址；Anthropic 留空使用官方地址"),
        ConfigField("api_key_env", "API Key 环境变量名", default="DEEPSEEK_API_KEY",
                    help="从服务所在机器的环境变量读取密钥，密钥不进入数据库（推荐）"),
        ConfigField("api_key", "API Key（可选）", secret=True, help="直接填写则加密存储并优先使用"),
        ConfigField("tools", "可用工具", "string_list", default=list(READ_TOOLS),
                    help=f"可选：{', '.join(ALL_TOOLS)}。write_file 可写工作目录，属于高风险"),
        ConfigField("system_prompt", "系统提示词", "text", default="你是一个严谨的 AI 助手。需要查看文件时使用工具，不要臆测文件内容。用中文回答。"),
        ConfigField("temperature", "temperature", "number", default=0.2),
        ConfigField("max_iterations", "最大推理轮数", "number", default=15),
        ConfigField("input_price_per_mtok", "输入价格 USD/百万 Token（可选）", "number", default=0,
                    help="填写后按 Token 计算成本；为 0 时成本显示为不可获取"),
        ConfigField("output_price_per_mtok", "输出价格 USD/百万 Token（可选）", "number", default=0),
    ]
    capabilities = {
        "connection_check": Level.supported,
        "send_task": Level.supported,
        "stream_events": Level.supported,
        "tool_events": Level.supported,
        "mcp_events": Level.unsupported,  # MCP tools via langchain-mcp-adapters arrive with MCP management (phase 4)
        "token_usage": Level.supported,
        "cost": Level.partial,  # only when per-token prices are configured
        "cancel": Level.supported,
        "permission_control": Level.supported,
        "session_resume": Level.unsupported,
        "model_select": Level.supported,
        "mcp_config": Level.unsupported,
    }

    _models_cache: dict[str, tuple[float, list[str]]] = {}

    @staticmethod
    def _api_key(config: dict, secrets: dict) -> str | None:
        return secrets.get("api_key") or (os.environ.get(config.get("api_key_env") or "") or None)

    async def _list_models(self, config: dict, key: str) -> tuple[bool, str, list[str]]:
        provider = config.get("provider") or "openai_compatible"
        if provider == "anthropic":
            url = (config.get("base_url") or "https://api.anthropic.com").rstrip("/") + "/v1/models"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            base = (config.get("base_url") or "").rstrip("/")
            if not base:
                return False, "未配置 API 地址", []
            url, headers = base + "/models", {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=not _is_loopback(url)) as c:
                r = await c.get(url, headers=headers)
        except httpx.HTTPError as e:
            return False, f"无法连接 {url}: {type(e).__name__}: {e}", []
        if r.status_code in (401, 403):
            return False, f"API Key 无效或无权限（HTTP {r.status_code}）", []
        if not r.is_success:
            return False, f"HTTP {r.status_code}: {r.text[:200]}", []
        try:
            models = sorted(m["id"] for m in r.json().get("data", []))
        except (ValueError, KeyError, TypeError):
            models = []
        self._models_cache[url] = (time.monotonic(), models)
        return True, f"接口可用，检测到 {len(models)} 个模型", models

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        key = self._api_key(config, secrets)
        if not key:
            return ConnectionResult(False, f"未配置 API Key：请填写，或在环境变量 {config.get('api_key_env') or '(未指定)'} 中设置")
        t0 = time.monotonic()
        ok, detail, models = await self._list_models(config, key)
        return ConnectionResult(ok, detail, version=f"langchain {langchain.__version__}",
                                latency_ms=int((time.monotonic() - t0) * 1000), detected={"models": models})

    async def validate_model(self, model: str, config: dict) -> str | None:
        if not model:
            return "LangChain Agent 必须指定模型（如 deepseek-flash）"
        return None

    def _llm(self, req: RunRequest, key: str):
        c = req.config
        if (c.get("provider") or "openai_compatible") == "anthropic":
            from langchain_anthropic import ChatAnthropic

            kw = {"base_url": c["base_url"]} if c.get("base_url") else {}
            return ChatAnthropic(model=req.model, api_key=key, temperature=c.get("temperature", 0.2),
                                 timeout=req.timeout_sec, max_retries=1, **kw)
        from langchain_openai import ChatOpenAI

        kw = {}
        if _is_loopback(c.get("base_url") or ""):
            kw["http_async_client"] = httpx.AsyncClient(trust_env=False, timeout=req.timeout_sec)
        return ChatOpenAI(model=req.model, base_url=c.get("base_url"), api_key=key, temperature=c.get("temperature", 0.2),
                          timeout=req.timeout_sec, max_retries=1, **kw)

    def build_model(self, req: RunRequest, key: str):
        """Overridable seam (tests inject a scripted chat model)."""
        return self._llm(req, key)

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        c = req.config
        key = self._api_key(c, req.secrets)
        if not key:
            return RunResult(ok=False, error=f"未配置 API Key（环境变量 {c.get('api_key_env')} 为空，且未填写密钥）")
        bad = [t for t in c.get("tools") or [] if t not in ALL_TOOLS]
        if bad:
            return RunResult(ok=False, error=f"未知工具: {bad}")
        tools = build_tools(req.workdir, c.get("tools") or [])
        agent = create_agent(self.build_model(req, key), tools, system_prompt=c.get("system_prompt") or None)
        cb = TraceCallback(emit, req.model)
        await emit(AdapterEvent(kind="session.started", name="langchain agent",
                                attributes={"model": req.model, "tools": [t.name for t in tools], "provider": c.get("provider"),
                                            "base_url": c.get("base_url")}))
        limit = int(c.get("max_iterations") or 15) * 2 + 1
        job = asyncio.create_task(agent.ainvoke({"messages": [{"role": "user", "content": req.prompt}]},
                                                config={"callbacks": [cb], "recursion_limit": limit}))
        waiter = asyncio.create_task(cancel.wait())
        done, _ = await asyncio.wait({job, waiter}, timeout=req.timeout_sec, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        result = RunResult(ok=False, model=req.model, input_tokens=cb.input_tokens, output_tokens=cb.output_tokens)
        if job not in done:
            job.cancel()
            result.cancelled = cancel.is_set()
            result.timed_out = not result.cancelled
            result.error = "已取消" if result.cancelled else f"执行超时（{req.timeout_sec}s）"
            return result
        try:
            state = job.result()
        except Exception as e:  # provider/network/recursion errors surface as a failed run
            name = type(e).__name__
            msg = f"超过最大推理轮数（{c.get('max_iterations')}）" if name == "GraphRecursionError" else f"{name}: {e}"
            result.error = msg[:1500]
            result.input_tokens, result.output_tokens = cb.input_tokens, cb.output_tokens
            return result
        final = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        content = final.content if final is not None else ""
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
        result.ok = True
        result.output = content or ""
        result.input_tokens, result.output_tokens = cb.input_tokens, cb.output_tokens
        pin, pout = float(c.get("input_price_per_mtok") or 0), float(c.get("output_price_per_mtok") or 0)
        if pin or pout:
            result.cost_usd = (cb.input_tokens * pin + cb.output_tokens * pout) / 1_000_000
        return result


__all__ = ["LangChainAdapter", "build_tools", "TraceCallback", "WorkspaceError"]
