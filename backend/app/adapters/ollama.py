"""Ollama adapter: local models through `/api/chat` (streaming NDJSON).

Ollama runs on the same box as the platform, so there is no cost and no API key —
just a base URL and a model name. Chunks are surfaced as `agent.message` events,
which is what makes the chat window stream tokens while a local model writes.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult

DEFAULT_BASE = "http://127.0.0.1:11434"


class OllamaStreamParser:
    """Turns `/api/chat` NDJSON lines into events plus a final RunResult."""

    def __init__(self) -> None:
        self.result = RunResult(ok=False)
        self.texts: list[str] = []
        self.done = False
        self.error: str | None = None

    def feed(self, line: str) -> list[AdapterEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return [AdapterEvent(kind="log", name="stdout", output=line[:400])]

        if data.get("error"):
            self.error = str(data["error"])[:1000]
            return [AdapterEvent(kind="agent.error", name="ollama", status="error", output=self.error)]

        out: list[AdapterEvent] = []
        message = data.get("message") or {}
        chunk = message.get("content") or ""
        if chunk:
            self.texts.append(chunk)
            out.append(AdapterEvent(kind="agent.message", name="assistant", output=chunk))

        if data.get("done"):
            self.done = True
            prompt_tokens = int(data.get("prompt_eval_count") or 0)
            eval_tokens = int(data.get("eval_count") or 0)
            total_ns = int(data.get("total_duration") or 0)
            self.result.input_tokens = prompt_tokens or None
            self.result.output_tokens = eval_tokens or None
            self.result.model = data.get("model") or self.result.model
            out.append(AdapterEvent(
                kind="model.call", name=self.result.model or "ollama",
                duration_ms=int(total_ns / 1_000_000) if total_ns else None,
                attributes={"input_tokens": prompt_tokens, "output_tokens": eval_tokens,
                            "total_duration_ms": int(total_ns / 1_000_000) if total_ns else None,
                            "eval_duration_ms": int((data.get("eval_duration") or 0) / 1_000_000)},
            ))
        return out

    def finish(self) -> RunResult:
        r = self.result
        r.output = "".join(self.texts)
        r.ok = self.done and not self.error
        if self.error:
            r.error = self.error
        elif not self.done:
            r.error = "Ollama 未返回 done 事件（连接中断或超时）"
        if r.ok and not r.output.strip():
            r.ok = False
            r.error = "Ollama 返回了空内容"
        return r


async def _get_json(url: str, timeout: float = 5.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


class OllamaAdapter(AgentAdapter):
    type = "ollama"
    display_name = "Ollama（本地模型）"
    platform = "Ollama"
    transport = "http"
    verified = True
    description = "调用本机 Ollama 的 /api/chat。免费、无需密钥，流式输出会实时显示在群聊与任务详情里。"
    config_fields = [
        ConfigField("base_url", "服务地址", default=DEFAULT_BASE, help="Ollama 默认监听 http://127.0.0.1:11434"),
        ConfigField("system_prompt", "系统提示词", type="text", default=""),
        ConfigField("temperature", "温度", type="number", default=0.2),
        ConfigField("num_ctx", "上下文长度", type="number", default=0,
                    help="0 表示用模型默认；小显存可设 4096 节省内存"),
        ConfigField("keep_alive", "模型驻留时长", default="5m", help="Ollama keep_alive，例如 5m / 30m"),
    ]
    capabilities = {
        "connection_check": Level.supported,
        "send_task": Level.supported,
        "stream_events": Level.supported,
        "tool_events": Level.unsupported,
        "mcp_events": Level.unsupported,
        "token_usage": Level.supported,
        "cost": Level.unsupported,
        "cancel": Level.supported,
        "permission_control": Level.unsupported,
        "session_resume": Level.unsupported,
        "model_select": Level.supported,
        "mcp_config": Level.unsupported,
    }

    def _base(self, config: dict) -> str:
        return (config.get("base_url") or DEFAULT_BASE).rstrip("/")

    async def list_models(self, config: dict) -> list[str]:
        data = await _get_json(f"{self._base(config)}/api/tags")
        return [m.get("name") for m in data.get("models") or [] if m.get("name")]

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        base = self._base(config)
        t0 = time.monotonic()
        try:
            version = (await _get_json(f"{base}/api/version")).get("version")
            models = await self.list_models(config)
        except Exception as exc:  # noqa: BLE001 - 让 UI 看到原因
            return ConnectionResult(False, f"无法连接 Ollama（{base}）：{type(exc).__name__}: {exc}")
        latency = int((time.monotonic() - t0) * 1000)
        detail = f"Ollama {version}，检测到 {len(models)} 个本地模型" if models else f"Ollama {version}，但还没有本地模型"
        return ConnectionResult(True, detail, version=version, latency_ms=latency,
                                detected={"base_url": base, "models": models})

    async def validate_model(self, model: str, config: dict) -> str | None:
        if not model:
            return "Ollama 必须指定模型，例如 qwen3.5:4b"
        try:
            models = await self.list_models(config)
        except Exception:
            return None  # 连不上时交给 check_connection 报错
        if models and model not in models:
            return f"Ollama 中没有模型「{model}」，可用：{', '.join(models[:8])}"
        return None

    def _body(self, req: RunRequest) -> dict:
        c = req.config
        messages = []
        if c.get("system_prompt"):
            messages.append({"role": "system", "content": c["system_prompt"]})
        messages.append({"role": "user", "content": req.prompt})
        options: dict = {}
        if c.get("temperature") is not None:
            options["temperature"] = float(c["temperature"])
        if c.get("num_ctx"):
            options["num_ctx"] = int(c["num_ctx"])
        body = {"model": req.model, "messages": messages, "stream": True}
        if options:
            body["options"] = options
        if c.get("keep_alive"):
            body["keep_alive"] = c["keep_alive"]
        return body

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        parser = OllamaStreamParser()
        parser.result.model = req.model
        url = f"{self._base(req.config)}/api/chat"
        timeout = httpx.Timeout(connect=10.0, read=req.timeout_sec, write=30.0, pool=10.0)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream("POST", url, json=self._body(req)) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:400]
                        return RunResult(ok=False, model=req.model,
                                         error=f"Ollama 返回 HTTP {resp.status_code}: {detail}")
                    async for line in resp.aiter_lines():
                        if cancel.is_set():
                            parser.result.cancelled = True
                            break
                        for event in parser.feed(line):
                            await emit(event)
        except httpx.ReadTimeout:
            parser.result.timed_out = True
        except Exception as exc:  # noqa: BLE001
            return RunResult(ok=False, model=req.model, error=f"{type(exc).__name__}: {exc}"[:600])

        result = parser.finish()
        result.model = req.model
        if parser.result.timed_out:
            result.ok, result.error = False, f"执行超时（{req.timeout_sec}s）"
        elif parser.result.cancelled:
            result.ok, result.error = False, "已取消"
        result.cost_usd = 0.0
        if not result.output:
            result.output = ""
        await emit(AdapterEvent(kind="agent.summary", name="ollama",
                                duration_ms=int((time.monotonic() - started) * 1000),
                                attributes={"chars": len(result.output)}))
        return result
