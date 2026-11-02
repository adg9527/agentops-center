"""Generic HTTP agent adapter for user-built agent services.

Contract: POST {url} with JSON body {<prompt_field>: prompt, ...extra_body}; the answer is read from
the response JSON at dot-path <output_path>. Execution internals are not observable via this
transport, so tool/token capabilities are declared unsupported/unknown.
"""

import asyncio
import time
from urllib.parse import urlparse

import httpx

from .base import AdapterEvent, AgentAdapter, ConfigField, ConnectionResult, Emit, Level, RunRequest, RunResult


def _client(url: str, timeout: float) -> httpx.AsyncClient:
    # Loopback agents must not be routed through a system HTTP(S)_PROXY.
    host = urlparse(url).hostname or ""
    local = host in ("localhost", "::1") or host.startswith("127.")
    return httpx.AsyncClient(timeout=timeout, trust_env=not local)


def dig(obj, path: str):
    for key in [p for p in path.split(".") if p]:
        if isinstance(obj, list) and key.isdigit():
            obj = obj[int(key)]
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


class HttpAgentAdapter(AgentAdapter):
    type = "http"
    display_name = "通用 HTTP Agent"
    platform = "Custom HTTP service"
    transport = "http"
    verified = True
    description = "向自研 Agent 服务发送 HTTP POST，并按 JSON 路径读取结果。无法获取内部执行事件。"
    config_fields = [
        ConfigField("url", "任务接口 URL", required=True, help="例如 http://127.0.0.1:9000/run"),
        ConfigField("health_url", "健康检查 URL", default="", help="GET，返回 2xx 视为在线；留空则对任务 URL 发 OPTIONS"),
        ConfigField("prompt_field", "Prompt 字段名", default="prompt"),
        ConfigField("output_path", "结果 JSON 路径", default="output", help="点分路径，如 data.answer 或 choices.0.text"),
        ConfigField("extra_body", "附加请求体", "json", default={}),
        ConfigField("auth_header", "认证 Header 名", default="Authorization"),
        ConfigField("auth_value", "认证 Header 值", secret=True, help="如 Bearer xxx，加密存储"),
    ]
    capabilities = {
        "connection_check": Level.supported,
        "send_task": Level.supported,
        "stream_events": Level.unsupported,
        "tool_events": Level.unsupported,
        "mcp_events": Level.unsupported,
        "token_usage": Level.unknown,
        "cost": Level.unknown,
        "cancel": Level.partial,  # cancels the client request; remote side may continue
        "permission_control": Level.unsupported,
        "session_resume": Level.unsupported,
        "model_select": Level.unknown,
        "mcp_config": Level.unsupported,
    }

    def _headers(self, config: dict, secrets: dict) -> dict:
        if secrets.get("auth_value"):
            return {config.get("auth_header") or "Authorization": secrets["auth_value"]}
        return {}

    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult:
        url = config.get("health_url") or config.get("url")
        if not url:
            return ConnectionResult(False, "未配置 URL")
        t0 = time.monotonic()
        try:
            async with _client(url, 10) as client:
                if config.get("health_url"):
                    resp = await client.get(url, headers=self._headers(config, secrets))
                else:
                    resp = await client.options(url, headers=self._headers(config, secrets))
        except httpx.HTTPError as e:
            return ConnectionResult(False, f"连接失败: {type(e).__name__}: {e}")
        latency = int((time.monotonic() - t0) * 1000)
        ok = resp.status_code < 500 if not config.get("health_url") else resp.is_success
        return ConnectionResult(ok, f"HTTP {resp.status_code}", latency_ms=latency, detected={"status_code": resp.status_code})

    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult:
        c = req.config
        body = dict(c.get("extra_body") or {})
        body[c.get("prompt_field") or "prompt"] = req.prompt
        await emit(AdapterEvent(kind="tool.call", name="http.post", status="started", input=c.get("url"), call_id="http"))
        t0 = time.monotonic()

        async def _post():
            async with _client(c["url"], req.timeout_sec) as client:
                return await client.post(c["url"], json=body, headers=self._headers(c, req.secrets))

        post = asyncio.create_task(_post())
        waiter = asyncio.create_task(cancel.wait())
        done, _ = await asyncio.wait({post, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if post not in done:
            post.cancel()
            return RunResult(ok=False, cancelled=True, error="已取消（远端可能仍在执行）")
        dur = int((time.monotonic() - t0) * 1000)
        try:
            resp = post.result()
        except httpx.TimeoutException:
            return RunResult(ok=False, timed_out=True, error=f"HTTP 超时（{req.timeout_sec}s）")
        except httpx.HTTPError as e:
            return RunResult(ok=False, error=f"HTTP 错误: {e}")
        await emit(AdapterEvent(kind="tool.result", name="http.post", status="ok" if resp.is_success else "error",
                                output=f"HTTP {resp.status_code}", call_id="http", duration_ms=dur))
        if not resp.is_success:
            return RunResult(ok=False, error=f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError:
            return RunResult(ok=False, error="响应不是 JSON")
        output = dig(data, c.get("output_path") or "output")
        if output is None:
            return RunResult(ok=False, error=f"响应中未找到路径 {c.get('output_path')}")
        text = output if isinstance(output, str) else str(output)
        await emit(AdapterEvent(kind="agent.message", name="response", output=text))
        return RunResult(ok=True, output=text)
