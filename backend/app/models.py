"""Phase-1 data model. See docs/02-模块拆分-数据模型-数据流.md for the full (multi-phase) design."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class Role(str, enum.Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    waiting_approval = "waiting_approval"
    waiting_handoff = "waiting_handoff"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    timeout = "timeout"


TERMINAL_TASK = {TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled}
TERMINAL_RUN = {RunStatus.completed, RunStatus.failed, RunStatus.cancelled, RunStatus.timeout}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.viewer.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(128))
    adapter_type: Mapped[str] = mapped_column(String(32))
    platform: Mapped[str] = mapped_column(String(64))
    transport: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(64), default="")
    capability_tags: Mapped[list] = mapped_column(default=list)
    config: Mapped[dict] = mapped_column(default=dict)
    secret_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    detected: Mapped[dict] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(16), default="unknown")
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(nullable=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=600)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    # Soft delete keeps run history and traces resolvable after an agent is removed.
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(256))
    prompt: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16), default="direct")
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.pending.value, index=True)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_dir: Mapped[str] = mapped_column(String(512), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_config_version: Mapped[int] = mapped_column(Integer, default=1)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.pending.value)
    span_id: Mapped[str] = mapped_column(String(16))
    external_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (Index("ix_trace_events_agent_ts", "agent_id", "timestamp"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    timestamp: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column("metadata", default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


# ---------------------------------------------------------------- phase 2: collaboration


class MemberRole(str, enum.Enum):
    analyst = "analyst"
    executor = "executor"
    reviewer = "reviewer"
    summarizer = "summarizer"


# Manual mode runs mentioned members stage by stage in this order.
ROLE_STAGE = {MemberRole.analyst.value: 1, MemberRole.executor.value: 2, MemberRole.reviewer.value: 3,
              MemberRole.summarizer.value: 4}


class StepStatus(str, enum.Enum):
    pending = "pending"
    waiting_approval = "waiting_approval"
    waiting_handoff = "waiting_handoff"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    skipped = "skipped"


TERMINAL_STEP = {StepStatus.completed, StepStatus.failed, StepStatus.cancelled, StepStatus.skipped}


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(128))
    coordinator_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    require_plan_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ConversationMember(Base):
    __tablename__ = "conversation_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    role: Mapped[str] = mapped_column(String(16), default=MemberRole.executor.value)
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender_type: Mapped[str] = mapped_column(String(8))  # user | agent | system
    sender_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | plan | handoff | approval | result | status
    content: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(default=list)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class TaskStep(Base):
    """One unit of collaborative work; executed as a subtask (Task with parent_task_id) by exactly one agent."""

    __tablename__ = "task_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    root_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    subtask_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    step_key: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default="work")  # plan | work | summary
    title: Mapped[str] = mapped_column(String(256))
    instruction: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default=MemberRole.executor.value)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    depends_on: Mapped[list] = mapped_column(default=list)  # step_keys
    status: Mapped[str] = mapped_column(String(20), default=StepStatus.pending.value)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(16), default="user")  # user | plan | handoff | intervention
    seq: Mapped[int] = mapped_column(Integer, default=0)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Handoff(Base):
    __tablename__ = "handoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    root_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    from_step_id: Mapped[int | None] = mapped_column(ForeignKey("task_steps.id"), nullable=True)
    to_step_id: Mapped[int | None] = mapped_column(ForeignKey("task_steps.id"), nullable=True)
    from_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    to_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    initiated_by: Mapped[str] = mapped_column(String(16), default="platform")  # platform | agent | user
    reason: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    done_work: Mapped[str] = mapped_column(Text, default="")
    remaining_work: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list] = mapped_column(default=list)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # proposed -> accepted (receiver started) -> completed (receiver finished) | rejected | cancelled
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    root_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("task_steps.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))  # plan | high_risk_step
    title: Mapped[str] = mapped_column(String(256))
    detail: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
