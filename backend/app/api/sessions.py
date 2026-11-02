"""Local agent session transcripts (Claude Code JSONL + OpenCode SQLite)."""

from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session as db_session
from ..models import Task, User
from ..services.local_sessions import (
    SOURCES,
    get_claude_session,
    get_opencode_session,
    list_sessions,
    normalize_path,
)
from .deps import current_user

router = APIRouter(prefix="/api/local-sessions", tags=["local-sessions"])

VALID = {"all", *SOURCES}


async def _attach_tasks(items: list[dict], session: AsyncSession) -> None:
    """Link a local session to the platform tasks that ran in the same directory."""
    dirs = {normalize_path(i.get("project_path")) for i in items if i.get("project_path")}
    if not dirs:
        for item in items:
            item["tasks"] = []
        return
    rows = (await session.execute(select(Task.id, Task.title, Task.status, Task.workspace_dir))).all()
    mapping: dict[str, list[dict]] = defaultdict(list)
    for task_id, title, status, workspace in rows:
        mapping[normalize_path(workspace)].append({"id": task_id, "title": title, "status": status})
    for item in items:
        item["tasks"] = sorted(mapping.get(normalize_path(item.get("project_path")), []),
                               key=lambda t: t["id"], reverse=True)[:5]


@router.get("")
async def list_local_sessions(source: str = "all", limit: int = 60, q: str = "",
                              _: User = Depends(current_user),
                              session: AsyncSession = Depends(db_session)):
    """本机 Agent CLI 自己保存的会话记录（Claude Code / OpenCode），按最后活动时间排序。"""
    if source not in VALID:
        raise HTTPException(422, f"暂不支持 source={source}（可用：{', '.join(sorted(VALID))}）")
    items = list_sessions(source=source, limit=min(max(limit, 1), 200), query=q)
    await _attach_tasks(items, session)
    return {"source": source, "sources": SOURCES, "items": items}


@router.get("/claude/{project}/{session_id}")
async def get_claude(project: str, session_id: str, _: User = Depends(current_user),
                     session: AsyncSession = Depends(db_session)):
    try:
        detail = get_claude_session(project, session_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    await _attach_tasks([detail], session)
    return detail


@router.get("/{source}/{session_id}")
async def get_other_session(source: str, session_id: str, _: User = Depends(current_user),
                            session: AsyncSession = Depends(db_session)):
    if source != "opencode":
        raise HTTPException(422, f"暂不支持 source={source}")
    try:
        detail = get_opencode_session(session_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    await _attach_tasks([detail], session)
    return detail
