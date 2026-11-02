"""Asset management: MCP servers, skills, prompt templates.

Registry + validation only — nothing here is silently injected into an agent's run;
the pages exist so a deployment can document and verify what is available, and so
later phases can reference these rows by id.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session, utcnow
from ..models import McpServer, PromptTemplate, Role, Skill, User
from ..services.audit import audit
from ..services.mcp_probe import probe
from .deps import current_user, require

router = APIRouter(prefix="/api", tags=["assets"])

_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def variables_of(content: str) -> list[str]:
    seen: list[str] = []
    for name in _VAR.findall(content or ""):
        if name not in seen:
            seen.append(name)
    return seen


def _mcp_dict(m: McpServer) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "transport": m.transport,
        "command": m.command,
        "args": m.args,
        "url": m.url,
        "headers": m.headers,
        "env": m.env,
        "enabled": m.enabled,
        "tools": m.tools,
        "tool_count": len(m.tools or []),
        "last_check_at": m.last_check_at.isoformat() if m.last_check_at else None,
        "last_check_ok": m.last_check_ok,
        "last_check_detail": m.last_check_detail,
        "config_version": m.config_version,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def _skill_dict(s: Skill) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "content": s.content,
        "files": s.files,
        "tags": s.tags,
        "version": s.version,
        "enabled": s.enabled,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _prompt_dict(p: PromptTemplate) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "content": p.content,
        "variables": p.variables,
        "tags": p.tags,
        "version": p.version,
        "enabled": p.enabled,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


async def _unique(session: AsyncSession, model, name: str, *, exclude_id: int | None = None) -> None:
    q = select(model.id).where(model.name == name, model.deleted.is_(False))
    if exclude_id:
        q = q.where(model.id != exclude_id)
    if await session.scalar(q):
        raise HTTPException(409, f"名称已存在：{name}")


# ------------------------------------------------------------------ MCP servers


class McpIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    transport: str = Field("stdio", pattern="^(stdio|streamable-http)$")
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class McpPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None


def _check_mcp(body) -> None:
    if body.transport == "stdio" and not (body.command or "").strip():
        raise HTTPException(422, "stdio 方式必须填写启动命令")
    if body.transport == "streamable-http" and not (body.url or "").strip():
        raise HTTPException(422, "streamable-http 方式必须填写 URL")


@router.get("/mcp-servers")
async def list_mcp(_: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(McpServer).where(McpServer.deleted.is_(False)).order_by(McpServer.id))).all()
    return [_mcp_dict(m) for m in rows]


@router.post("/mcp-servers")
async def create_mcp(body: McpIn, request: Request, user: User = Depends(require(Role.operator)),
                     session: AsyncSession = Depends(get_session)):
    _check_mcp(body)
    await _unique(session, McpServer, body.name)
    m = McpServer(**body.model_dump())
    session.add(m)
    await session.flush()
    await audit(session, "mcp.create", user=user, target_type="mcp", target_id=m.id, request=request,
                detail={"name": m.name, "transport": m.transport})
    return _mcp_dict(m)


async def _get_mcp(session: AsyncSession, server_id: int) -> McpServer:
    m = await session.get(McpServer, server_id)
    if not m or m.deleted:
        raise HTTPException(404, "MCP Server 不存在")
    return m


@router.patch("/mcp-servers/{server_id}")
async def update_mcp(server_id: int, body: McpPatch, request: Request, user: User = Depends(require(Role.operator)),
                     session: AsyncSession = Depends(get_session)):
    m = await _get_mcp(session, server_id)
    changed = body.model_dump(exclude_unset=True)
    if "name" in changed:
        await _unique(session, McpServer, changed["name"], exclude_id=m.id)
    for key, value in changed.items():
        setattr(m, key, value)
    _check_mcp(m)
    m.config_version += 1
    await audit(session, "mcp.update", user=user, target_type="mcp", target_id=m.id, request=request,
                detail={"changed": sorted(changed)})
    return _mcp_dict(m)


@router.delete("/mcp-servers/{server_id}")
async def delete_mcp(server_id: int, request: Request, user: User = Depends(require(Role.admin)),
                     session: AsyncSession = Depends(get_session)):
    m = await _get_mcp(session, server_id)
    m.deleted = True
    await audit(session, "mcp.delete", user=user, target_type="mcp", target_id=m.id, request=request,
                detail={"name": m.name})
    return {"ok": True}


@router.post("/mcp-servers/{server_id}/test")
async def test_mcp(server_id: int, request: Request, user: User = Depends(require(Role.operator)),
                   session: AsyncSession = Depends(get_session)):
    m = await _get_mcp(session, server_id)
    result = await probe({"transport": m.transport, "url": m.url, "headers": m.headers,
                          "command": m.command, "args": m.args, "env": m.env})
    m.last_check_at = utcnow()
    m.last_check_ok = bool(result["ok"])
    m.last_check_detail = str(result.get("detail") or "")[:2000]
    if result["ok"]:
        m.tools = result["tools"]
    await audit(session, "mcp.test", user=user, target_type="mcp", target_id=m.id, request=request,
                detail={"ok": result["ok"], "tools": len(result["tools"])})
    return {**result, "server": _mcp_dict(m)}


# ----------------------------------------------------------------------- skills


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    content: str = ""
    files: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class SkillPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    files: list[dict] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


@router.get("/skills")
async def list_skills(_: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Skill).where(Skill.deleted.is_(False)).order_by(Skill.id))).all()
    return [_skill_dict(s) for s in rows]


@router.post("/skills")
async def create_skill(body: SkillIn, request: Request, user: User = Depends(require(Role.operator)),
                       session: AsyncSession = Depends(get_session)):
    await _unique(session, Skill, body.name)
    s = Skill(**body.model_dump())
    session.add(s)
    await session.flush()
    await audit(session, "skill.create", user=user, target_type="skill", target_id=s.id, request=request,
                detail={"name": s.name})
    return _skill_dict(s)


async def _get_skill(session: AsyncSession, skill_id: int) -> Skill:
    s = await session.get(Skill, skill_id)
    if not s or s.deleted:
        raise HTTPException(404, "Skill 不存在")
    return s


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return _skill_dict(await _get_skill(session, skill_id))


@router.patch("/skills/{skill_id}")
async def update_skill(skill_id: int, body: SkillPatch, request: Request, user: User = Depends(require(Role.operator)),
                       session: AsyncSession = Depends(get_session)):
    s = await _get_skill(session, skill_id)
    changed = body.model_dump(exclude_unset=True)
    if "name" in changed:
        await _unique(session, Skill, changed["name"], exclude_id=s.id)
    for key, value in changed.items():
        setattr(s, key, value)
    s.version += 1
    await audit(session, "skill.update", user=user, target_type="skill", target_id=s.id, request=request,
                detail={"changed": sorted(changed)})
    return _skill_dict(s)


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: int, request: Request, user: User = Depends(require(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    s = await _get_skill(session, skill_id)
    s.deleted = True
    await audit(session, "skill.delete", user=user, target_type="skill", target_id=s.id, request=request,
                detail={"name": s.name})
    return {"ok": True}


# ---------------------------------------------------------------------- prompts


class PromptIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class PromptPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class RenderIn(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)


@router.get("/prompts")
async def list_prompts(_: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(PromptTemplate).where(PromptTemplate.deleted.is_(False)).order_by(PromptTemplate.id))).all()
    return [_prompt_dict(p) for p in rows]


@router.post("/prompts")
async def create_prompt(body: PromptIn, request: Request, user: User = Depends(require(Role.operator)),
                        session: AsyncSession = Depends(get_session)):
    await _unique(session, PromptTemplate, body.name)
    p = PromptTemplate(**body.model_dump(), variables=variables_of(body.content))
    session.add(p)
    await session.flush()
    await audit(session, "prompt.create", user=user, target_type="prompt", target_id=p.id, request=request,
                detail={"name": p.name, "variables": p.variables})
    return _prompt_dict(p)


async def _get_prompt(session: AsyncSession, prompt_id: int) -> PromptTemplate:
    p = await session.get(PromptTemplate, prompt_id)
    if not p or p.deleted:
        raise HTTPException(404, "Prompt 不存在")
    return p


@router.get("/prompts/{prompt_id}")
async def get_prompt(prompt_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return _prompt_dict(await _get_prompt(session, prompt_id))


@router.patch("/prompts/{prompt_id}")
async def update_prompt(prompt_id: int, body: PromptPatch, request: Request,
                        user: User = Depends(require(Role.operator)), session: AsyncSession = Depends(get_session)):
    p = await _get_prompt(session, prompt_id)
    changed = body.model_dump(exclude_unset=True)
    if "name" in changed:
        await _unique(session, PromptTemplate, changed["name"], exclude_id=p.id)
    for key, value in changed.items():
        setattr(p, key, value)
    p.variables = variables_of(p.content)
    p.version += 1
    await audit(session, "prompt.update", user=user, target_type="prompt", target_id=p.id, request=request,
                detail={"changed": sorted(changed)})
    return _prompt_dict(p)


@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: int, request: Request, user: User = Depends(require(Role.admin)),
                        session: AsyncSession = Depends(get_session)):
    p = await _get_prompt(session, prompt_id)
    p.deleted = True
    await audit(session, "prompt.delete", user=user, target_type="prompt", target_id=p.id, request=request,
                detail={"name": p.name})
    return {"ok": True}


@router.post("/prompts/{prompt_id}/render")
async def render_prompt(prompt_id: int, body: RenderIn, _: User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    p = await _get_prompt(session, prompt_id)
    values = body.variables or {}
    rendered = _VAR.sub(lambda m: values.get(m.group(1), m.group(0)), p.content)
    missing = [v for v in variables_of(p.content) if v not in values]
    return {"rendered": rendered, "missing": missing, "variables": p.variables}
