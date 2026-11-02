"""Persist OTel-style span events and fan them out to live subscribers."""

import hashlib
import secrets as _secrets

from ..db import session_factory, utcnow
from ..models import TraceEvent
from ..security import redact
from .event_bus import bus


def new_trace_id() -> str:
    return _secrets.token_hex(16)


def new_span_id() -> str:
    return _secrets.token_hex(8)


def root_span_id(trace_id: str) -> str:
    return trace_id[:16]


def task_span_id(task) -> str:
    """Root tasks own the trace's root span; subtasks get a stable span derived from their id."""
    if not task.parent_task_id:
        return root_span_id(task.trace_id)
    return hashlib.sha1(f"{task.trace_id}:{task.id}".encode()).hexdigest()[:16]


def event_to_dict(e: TraceEvent) -> dict:
    return {
        "event_id": e.event_id,
        "trace_id": e.trace_id,
        "span_id": e.span_id,
        "parent_span_id": e.parent_span_id,
        "task_id": e.task_id,
        "run_id": e.run_id,
        "agent_id": e.agent_id,
        "event_type": e.event_type,
        "name": e.name,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "status": e.status,
        "duration_ms": e.duration_ms,
        "input_summary": e.input_summary,
        "output_summary": e.output_summary,
        "metadata": e.attributes,
    }


async def record_event(
    *,
    trace_id: str,
    span_id: str,
    event_type: str,
    parent_span_id: str | None = None,
    task_id: int | None = None,
    run_id: int | None = None,
    agent_id: int | None = None,
    name: str = "",
    status: str = "ok",
    duration_ms: int | None = None,
    input_summary: str | None = None,
    output_summary: str | None = None,
    attributes: dict | None = None,
    known_secrets: list[str] | None = None,
) -> dict:
    ev = TraceEvent(
        event_id=_secrets.token_hex(16),
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        event_type=event_type,
        name=(name or "")[:256],
        timestamp=utcnow(),
        status=status,
        duration_ms=duration_ms,
        input_summary=redact(input_summary, known_secrets),
        output_summary=redact(output_summary, known_secrets),
        attributes=attributes or {},
    )
    async with session_factory()() as s:
        s.add(ev)
        await s.commit()
    data = event_to_dict(ev)
    bus.publish("trace_event", data)
    return data
