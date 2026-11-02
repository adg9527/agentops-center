"""Unified Agent Adapter contract.

Every external agent platform is wrapped by an adapter that:
  * declares its transport and per-capability support level (never claims what it can't do),
  * checks connectivity / detects capabilities,
  * runs a task, streaming normalized AdapterEvents back through `emit`,
  * honours a cancel signal and returns a RunResult.
"""

import asyncio
import enum
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Level(str, enum.Enum):
    supported = "supported"
    partial = "partial"
    unsupported = "unsupported"
    unknown = "unknown"


# Capability keys shown in the UI (labels live in the frontend).
CAPABILITIES = [
    "connection_check",
    "send_task",
    "stream_events",
    "tool_events",
    "mcp_events",
    "token_usage",
    "cost",
    "cancel",
    "permission_control",
    "session_resume",
    "model_select",
    "mcp_config",
]


@dataclass
class ConfigField:
    key: str
    label: str
    type: str = "string"  # string | text | number | bool | string_list | select | json
    required: bool = False
    default: Any = None
    secret: bool = False
    help: str = ""
    options: list[str] | None = None


@dataclass
class ConnectionResult:
    ok: bool
    detail: str
    version: str | None = None
    latency_ms: int | None = None
    detected: dict = field(default_factory=dict)


@dataclass
class AdapterEvent:
    """Normalized event; TraceService converts it to a span event row."""

    kind: str  # session.started | agent.message | model.call | tool.call | tool.result | permission.denied | log
    name: str = ""
    status: str = "ok"
    input: str | None = None
    output: str | None = None
    call_id: str | None = None  # links tool.call <-> tool.result
    duration_ms: int | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class RunRequest:
    prompt: str
    workdir: Path
    config: dict
    secrets: dict
    model: str
    timeout_sec: int


@dataclass
class RunResult:
    ok: bool
    output: str = ""
    error: str | None = None
    session_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    timed_out: bool = False
    cancelled: bool = False


Emit = Callable[[AdapterEvent], Awaitable[None]]


class AgentAdapter(ABC):
    type: str
    display_name: str
    platform: str
    transport: str  # cli | http | mcp | sdk | webhook | a2a
    verified: bool  # True only when exercised end-to-end in this environment
    description: str = ""
    config_fields: list[ConfigField] = []
    capabilities: dict[str, Level] = {}

    @abstractmethod
    async def check_connection(self, config: dict, secrets: dict) -> ConnectionResult: ...

    @abstractmethod
    async def run(self, req: RunRequest, emit: Emit, cancel: asyncio.Event) -> RunResult: ...

    async def validate_model(self, model: str, config: dict) -> str | None:
        """Return a user-facing error if `model` can't work with this platform, else None."""
        return None

    async def normalize_model(self, model: str, config: dict) -> str:
        """Canonicalize a model id before it is stored or used (default: unchanged)."""
        return model

    def describe(self) -> dict:
        return {
            "type": self.type,
            "display_name": self.display_name,
            "platform": self.platform,
            "transport": self.transport,
            "verified": self.verified,
            "description": self.description,
            "config_fields": [f.__dict__ for f in self.config_fields],
            "capabilities": {k: self.capabilities.get(k, Level.unknown).value for k in CAPABILITIES},
        }

    def defaults(self) -> dict:
        return {f.key: f.default for f in self.config_fields if not f.secret and f.default is not None}
