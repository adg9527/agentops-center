"""Local agent session transcripts (Claude Code's own JSONL history)."""

from fastapi import APIRouter, Depends, HTTPException

from ..models import User
from ..services.local_sessions import get_session, list_sessions
from .deps import current_user

router = APIRouter(prefix="/api/local-sessions", tags=["local-sessions"])

SOURCES = {"claude": "Claude Code"}


@router.get("")
async def list_local_sessions(source: str = "claude", limit: int = 60, q: str = "",
                              _: User = Depends(current_user)):
    """本机 Agent CLI 自己保存的会话记录（当前支持 Claude Code）。"""
    if source not in SOURCES:
        raise HTTPException(422, f"暂不支持 source={source}（可用：{', '.join(SOURCES)}）")
    return {"source": source, "items": list_sessions(limit=min(max(limit, 1), 200), query=q)}


@router.get("/{source}/{project}/{session}")
async def get_local_session(source: str, project: str, session: str, _: User = Depends(current_user)):
    if source not in SOURCES:
        raise HTTPException(422, f"暂不支持 source={source}")
    try:
        return get_session(project, session)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
