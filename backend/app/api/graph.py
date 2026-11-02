"""Read-only graph endpoints for the workbench (relationship graph + mind map)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import User
from ..services.graph import task_graph, task_mindmap
from .deps import current_user

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/task/{task_id}")
async def get_task_graph(task_id: int, _: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    """任务关系图：任务 → 步骤 → 参与者，含依赖与交接边、进度统计。"""
    try:
        return await task_graph(session, task_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/task/{task_id}/mindmap")
async def get_task_mindmap(task_id: int, _: User = Depends(current_user),
                           session: AsyncSession = Depends(get_session)):
    """任务思维导图：参与者层级 → 调用过的工具 → 涉及的文件。"""
    try:
        return await task_mindmap(session, task_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
