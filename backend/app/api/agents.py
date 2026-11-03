from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters import ADAPTERS, get_adapter
from ..adapters.base import AgentAdapter
from ..adapters.langchain_agent import WRITE_TOOLS, LangChainAdapter
from ..adapters.opencode import OpenCodeAdapter, resolve_opencode
from ..db import get_session, utcnow
from ..models import Agent, AgentRun, Project, Role, RunStatus, Task, User
from ..security import decrypt_secrets, encrypt_secrets
from ..services.audit import audit
from ..services.event_bus import bus
from ..services.task_runner import run_to_dict
from .deps import current_user, require

router = APIRouter(prefix="/api", tags=["agents"])

_HIGH_RISK_CLAUDE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "WebFetch"}


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    adapter_type: str
    project_id: int | None = None
    description: str = ""
    role: str = ""
    capability_tags: list[str] = []
    config: dict = {}
    secrets: dict[str, str | None] = {}
    model: str = ""
    enabled: bool = True
    max_concurrency: int = Field(1, ge=1, le=16)
    timeout_sec: int = Field(600, ge=10, le=24 * 3600)
    max_retries: int = Field(0, ge=0, le=5)


class AgentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    role: str | None = None
    capability_tags: list[str] | None = None
    config: dict | None = None
    secrets: dict[str, str | None] | None = None  # value null => remove; omitted key => keep
    model: str | None = None
    max_concurrency: int | None = Field(None, ge=1, le=16)
    timeout_sec: int | None = Field(None, ge=10, le=24 * 3600)
    max_retries: int | None = Field(None, ge=0, le=5)


def risk_warnings(adapter_type: str, config: dict) -> list[str]:
    w = []
    if adapter_type == "claude_code":
        risky = (set(config.get("tools") or []) | set(config.get("allowed_tools") or [])) & _HIGH_RISK_CLAUDE_TOOLS
        if risky:
            w.append(f"开放了高风险工具: {', '.join(sorted(risky))}")
        if not config.get("isolate", True):
            w.append("未隔离用户级配置：Agent 将加载本机全局插件/MCP")
    elif adapter_type == "claude_sdk":
        if config.get("permission_mode") == "bypassPermissions":
            w.append("权限模式为 bypassPermissions：工具调用不再询问，可直接改文件/执行命令")
        risky = set(config.get("allowed_tools") or []) & _HIGH_RISK_CLAUDE_TOOLS
        if risky:
            w.append(f"开放了高风险工具: {', '.join(sorted(risky))}")
    elif adapter_type == "opencode":
        allowed = [k for k, v in (config.get("permission") or {}).items() if v == "allow"]
        if allowed:
            w.append(f"OpenCode 权限设为 allow: {', '.join(allowed)}")
    elif adapter_type == "codex" and config.get("sandbox") == "workspace-write":
        w.append("Codex 沙箱为 workspace-write，可修改工作目录")
    elif adapter_type == "langchain" and set(config.get("tools") or []) & set(WRITE_TOOLS):
        w.append("LangChain Agent 开放了写文件工具 write_file")
    return w


def _validate_config(adapter: AgentAdapter, config: dict) -> dict:
    merged = {**adapter.defaults(), **{k: v for k, v in config.items() if not _is_secret(adapter, k)}}
    missing = [f.label for f in adapter.config_fields if f.required and not merged.get(f.key)]
    if missing:
        raise HTTPException(422, f"缺少必填配置: {', '.join(missing)}")
    return merged


def _is_secret(adapter: AgentAdapter, key: str) -> bool:
    return any(f.key == key and f.secret for f in adapter.config_fields)


def agent_dict(a: Agent) -> dict:
    adapter = ADAPTERS.get(a.adapter_type)
    return {
        "id": a.id,
        "project_id": a.project_id,
        "name": a.name,
        "adapter_type": a.adapter_type,
        "adapter_name": adapter.display_name if adapter else a.adapter_type,
        "adapter_verified": adapter.verified if adapter else False,
        "platform": a.platform,
        "transport": a.transport,
        "description": a.description,
        "role": a.role,
        "capability_tags": a.capability_tags,
        "config": a.config,
        "secret_keys_set": sorted(decrypt_secrets(a.secret_config).keys()),  # names only, never values
        "model": a.model,
        "detected": a.detected,
        "capabilities": adapter.describe()["capabilities"] if adapter else {},
        "enabled": a.enabled,
        "health": a.health,
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "last_check_at": a.last_check_at.isoformat() if a.last_check_at else None,
        "max_concurrency": a.max_concurrency,
        "timeout_sec": a.timeout_sec,
        "max_retries": a.max_retries,
        "config_version": a.config_version,
        "risk_warnings": risk_warnings(a.adapter_type, a.config),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def _get_agent(session: AsyncSession, agent_id: int) -> Agent:
    a = await session.get(Agent, agent_id)
    if not a or a.deleted:
        raise HTTPException(404, "Agent 不存在")
    return a


@router.get("/adapters")
async def list_adapters(_: User = Depends(current_user)):
    return [a.describe() for a in ADAPTERS.values()]


@router.get("/adapters/{adapter_type}/models")
async def adapter_models(adapter_type: str, provider: str = "openai_compatible", base_url: str = "", api_key_env: str = "",
                         _: User = Depends(current_user)):
    """Models the platform can actually enumerate; empty when not discoverable.

    OpenCode: `opencode models`. LangChain: the provider's /models endpoint, using only an environment-variable key
    (a key typed into the form is not sent here; it becomes usable after saving).
    """
    adapter = ADAPTERS.get(adapter_type)
    if isinstance(adapter, OpenCodeAdapter):
        exe = resolve_opencode(None)
        return {"discoverable": True, "models": await adapter.list_models(exe) if exe else []}
    if getattr(adapter, "type", "") == "ollama":
        from ..adapters.ollama import DEFAULT_BASE

        try:
            models = await adapter.list_models({"base_url": base_url or DEFAULT_BASE})
            return {"discoverable": True, "models": models}
        except Exception as exc:  # noqa: BLE001
            return {"discoverable": True, "models": [], "detail": f"连接 Ollama 失败：{exc}"}
    if isinstance(adapter, LangChainAdapter):
        cfg = {"provider": provider, "base_url": base_url, "api_key_env": api_key_env}
        key = adapter._api_key(cfg, {})
        if not key:
            return {"discoverable": True, "models": [], "detail": f"环境变量 {api_key_env or '(未指定)'} 未设置"}
        ok, detail, models = await adapter._list_models(cfg, key)
        return {"discoverable": True, "models": models, "detail": detail}
    return {"discoverable": False, "models": []}


@router.get("/agents")
async def list_agents(project_id: int | None = None, _: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    q = select(Agent).where(Agent.deleted.is_(False)).order_by(Agent.id)
    if project_id:
        q = q.where(Agent.project_id == project_id)
    return [agent_dict(a) for a in (await session.scalars(q)).all()]


@router.post("/agents")
async def create_agent(body: AgentIn, request: Request, user: User = Depends(require(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    try:
        adapter = get_adapter(body.adapter_type)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    project_id = body.project_id or await session.scalar(select(func.min(Project.id)))
    if not project_id or not await session.get(Project, project_id):
        raise HTTPException(422, "项目不存在")
    config = _validate_config(adapter, body.config)
    model = await adapter.normalize_model(body.model.strip(), config)
    if err := await adapter.validate_model(model, config):
        raise HTTPException(422, err)
    a = Agent(
        project_id=project_id, name=body.name, adapter_type=adapter.type, platform=adapter.platform,
        transport=adapter.transport, description=body.description, role=body.role,
        capability_tags=body.capability_tags, config=config,
        secret_config=encrypt_secrets({k: v for k, v in body.secrets.items() if v and _is_secret(adapter, k)}),
        model=model, enabled=body.enabled, max_concurrency=body.max_concurrency,
        timeout_sec=body.timeout_sec, max_retries=body.max_retries,
    )
    session.add(a)
    await session.flush()
    await audit(session, "agent.create", user=user, target_type="agent", target_id=a.id, request=request,
                detail={"name": a.name, "adapter": a.adapter_type, "secret_keys": sorted(body.secrets.keys()),
                        "risk_warnings": risk_warnings(a.adapter_type, config)})
    return agent_dict(a)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return agent_dict(await _get_agent(session, agent_id))


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: int, body: AgentPatch, request: Request, user: User = Depends(require(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    a = await _get_agent(session, agent_id)
    adapter = get_adapter(a.adapter_type)
    changed = body.model_dump(exclude_unset=True)
    runtime_changed = False
    for key in ("name", "description", "role", "capability_tags", "max_concurrency", "timeout_sec", "max_retries"):
        if key in changed:
            setattr(a, key, changed[key])
    if "config" in changed:
        a.config = _validate_config(adapter, {**a.config, **body.config})
        runtime_changed = True
    if "model" in changed:
        model = await adapter.normalize_model((body.model or "").strip(), a.config)
        if err := await adapter.validate_model(model, a.config):
            raise HTTPException(422, err)
        a.model = model
        runtime_changed = True
    if body.secrets is not None:
        current = decrypt_secrets(a.secret_config)
        for k, v in body.secrets.items():
            if not _is_secret(adapter, k):
                continue
            if v is None:
                current.pop(k, None)
            elif v:
                current[k] = v
        a.secret_config = encrypt_secrets(current)
        runtime_changed = True
    if runtime_changed:
        a.config_version += 1
    detail = {k: v for k, v in changed.items() if k != "secrets"}
    if body.secrets is not None:
        detail["secret_keys_changed"] = sorted(body.secrets.keys())
    detail["risk_warnings"] = risk_warnings(a.adapter_type, a.config)
    await audit(session, "agent.update", user=user, target_type="agent", target_id=a.id, detail=detail, request=request)
    bus.publish("agent_updated", {"id": a.id})
    return agent_dict(a)


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: int, request: Request, user: User = Depends(require(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    a = await _get_agent(session, agent_id)
    active = await session.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.agent_id == a.id, AgentRun.status.in_([RunStatus.pending.value, RunStatus.running.value])
        )
    )
    if active:
        raise HTTPException(409, "该 Agent 有执行中的任务，请先终止任务")
    a.deleted, a.enabled = True, False
    await audit(session, "agent.delete", user=user, target_type="agent", target_id=a.id, detail={"name": a.name},
                request=request)
    bus.publish("agent_updated", {"id": a.id})
    return {"ok": True}


async def _set_enabled(agent_id: int, enabled: bool, request: Request, user: User, session: AsyncSession):
    a = await _get_agent(session, agent_id)
    a.enabled = enabled
    await audit(session, "agent.enable" if enabled else "agent.disable", user=user, target_type="agent",
                target_id=a.id, request=request)
    bus.publish("agent_updated", {"id": a.id})
    return agent_dict(a)


@router.post("/agents/{agent_id}/enable")
async def enable_agent(agent_id: int, request: Request, user: User = Depends(require(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    return await _set_enabled(agent_id, True, request, user, session)


@router.post("/agents/{agent_id}/disable")
async def disable_agent(agent_id: int, request: Request, user: User = Depends(require(Role.admin)),
                        session: AsyncSession = Depends(get_session)):
    return await _set_enabled(agent_id, False, request, user, session)


@router.post("/agents/{agent_id}/test")
async def test_agent(agent_id: int, request: Request, user: User = Depends(require(Role.operator)),
                     session: AsyncSession = Depends(get_session)):
    a = await _get_agent(session, agent_id)
    adapter = get_adapter(a.adapter_type)
    res = await adapter.check_connection(a.config, decrypt_secrets(a.secret_config))
    now = utcnow()
    a.last_check_at = now
    a.health = "online" if res.ok else "offline"
    if res.ok:
        a.last_seen_at = now
    a.detected = {**(a.detected or {}), "check": res.detected, "version": res.version, "checked_at": now.isoformat()}
    await audit(session, "agent.test", user=user, target_type="agent", target_id=a.id, request=request,
                detail={"ok": res.ok, "detail": res.detail[:300]})
    bus.publish("agent_updated", {"id": a.id})
    return {"ok": res.ok, "detail": res.detail, "version": res.version, "latency_ms": res.latency_ms,
            "detected": res.detected, "agent": agent_dict(a)}


@router.get("/agents/{agent_id}/runs")
async def agent_runs(agent_id: int, limit: int = 50, _: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(AgentRun, Task.title)
            .join(Task, Task.id == AgentRun.task_id)
            .where(AgentRun.agent_id == agent_id)
            .order_by(AgentRun.id.desc())
            .limit(min(limit, 500))
        )
    ).all()
    return [{**run_to_dict(r), "task_title": title} for r, title in rows]


@router.get("/agents/{agent_id}/stats")
async def agent_stats(agent_id: int, days: int = 30, _: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    base = select(AgentRun).where(AgentRun.agent_id == agent_id, AgentRun.created_at >= since)
    runs = (await session.scalars(base)).all()
    by_status: dict[str, int] = {}
    daily: dict[str, dict[str, int]] = {}
    for r in runs:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        day = r.created_at.date().isoformat()
        daily.setdefault(day, {})
        daily[day][r.status] = daily[day].get(r.status, 0) + 1
    finished = [r for r in runs if r.duration_ms is not None]
    done = by_status.get("completed", 0)
    terminal = sum(v for k, v in by_status.items() if k not in ("pending", "running"))
    return {
        "days": days,
        "total": len(runs),
        "by_status": by_status,
        "success_rate": (done / terminal) if terminal else None,
        "avg_duration_ms": (sum(r.duration_ms for r in finished) / len(finished)) if finished else None,
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "cost_usd": sum(r.cost_usd or 0 for r in runs),
        "cost_known_runs": sum(r.cost_usd is not None for r in runs),
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
    }
