"""Probe an MCP server (stdio or Streamable HTTP) and list its tools.

Used by the MCP management page so a saved server can be verified before an agent
depends on it. Speaks the same JSON-RPC handshake by hand instead of spawning a
full SDK client session, because the probe must work with arbitrary endpoints —
including ones that require the platform's own bearer token.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time

import httpx

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "agentops-center", "version": "0.1.0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _parse_body(text: str, content_type: str) -> dict | None:
    """MCP answers either plain JSON or one SSE event per message."""
    if "text/event-stream" in (content_type or ""):
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def probe_http(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict:
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "User-Agent": "agentops-center/0.1"}
    merged = {**base, **(headers or {})}
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(url, json=INIT, headers=merged)
        if resp.status_code >= 400:
            return {"ok": False, "detail": f"initialize 返回 HTTP {resp.status_code}: {resp.text[:300]}",
                    "tools": [], "latency_ms": int((time.monotonic() - started) * 1000)}
        init = _parse_body(resp.text, resp.headers.get("content-type", ""))
        if not init or "result" not in init:
            return {"ok": False, "detail": f"initialize 响应无法解析: {resp.text[:300]}",
                    "tools": [], "latency_ms": int((time.monotonic() - started) * 1000)}

        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            merged["mcp-session-id"] = session_id
        try:
            await client.post(url, json=INITIALIZED, headers=merged)
        except httpx.HTTPError:
            pass  # optional notification; some servers answer 202/204 or not at all

        listing = await client.post(url, json=TOOLS_LIST, headers=merged)
        payload = _parse_body(listing.text, listing.headers.get("content-type", ""))
        tools = ((payload or {}).get("result") or {}).get("tools") or []
        info = (init.get("result") or {}).get("serverInfo") or {}
        return {
            "ok": True,
            "detail": f"{info.get('name', 'mcp-server')} {info.get('version', '')}".strip(),
            "tools": [{"name": t.get("name"), "description": (t.get("description") or "")[:300]} for t in tools],
            "latency_ms": int((time.monotonic() - started) * 1000),
        }


async def _read_json(reader: asyncio.StreamReader, want_id: int, deadline: float) -> dict | None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        if not line:
            return None
        try:
            message = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if message.get("id") == want_id:
            return message


async def probe_stdio(command: str, args: list[str] | None = None, env: dict[str, str] | None = None,
                      timeout: float = 20.0) -> dict:
    args = list(args or [])
    started = time.monotonic()
    exe = shutil.which(command) or command
    argv = [exe, *args]
    if exe.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c", exe, *args]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )
    except (OSError, FileNotFoundError) as exc:
        return {"ok": False, "detail": f"无法启动进程：{exc}", "tools": [], "latency_ms": 0}

    deadline = time.monotonic() + timeout

    async def send(payload: dict) -> None:
        assert proc and proc.stdin
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()

    try:
        await send(INIT)
        init = await _read_json(proc.stdout, 1, deadline)  # type: ignore[arg-type]
        if not init:
            return {"ok": False, "detail": "initialize 超时或进程无输出", "tools": [],
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        await send(INITIALIZED)
        await send(TOOLS_LIST)
        listing = await _read_json(proc.stdout, 2, deadline)  # type: ignore[arg-type]
        tools = (((listing or {}).get("result") or {}).get("tools")) or []
        info = (init.get("result") or {}).get("serverInfo") or {}
        return {
            "ok": True,
            "detail": f"{info.get('name', 'mcp-server')} {info.get('version', '')}".strip(),
            "tools": [{"name": t.get("name"), "description": (t.get("description") or "")[:300]} for t in tools],
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except asyncio.TimeoutError:
        return {"ok": False, "detail": f"握手超时（{timeout:g}s）", "tools": [],
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 - surface any transport failure to the UI
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}", "tools": [],
                "latency_ms": int((time.monotonic() - started) * 1000)}
    finally:
        if proc and proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass


async def probe(config: dict) -> dict:
    if (config.get("transport") or "stdio") == "streamable-http":
        if not config.get("url"):
            return {"ok": False, "detail": "缺少 URL", "tools": [], "latency_ms": 0}
        return await probe_http(config["url"], config.get("headers") or {})
    if not config.get("command"):
        return {"ok": False, "detail": "缺少启动命令", "tools": [], "latency_ms": 0}
    return await probe_stdio(config["command"], config.get("args") or [], config.get("env") or {})
