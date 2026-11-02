"""Task artifacts: file list, version history, diff, restore."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import ArtifactVersion, Role, Task, User
from ..services.artifacts import (
    diff_text,
    get_version,
    list_files,
    restore_version,
    snapshot_task,
    versions_for,
    _version_dict,
)
from ..services.audit import audit
from .deps import current_user, require

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class SnapshotIn(BaseModel):
    paths: list[str] | None = None
    note: str = "manual"


@router.get("/tasks")
async def list_artifact_tasks(_: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """有产物的任务（工作目录里还有文件的）。"""
    counts = dict((tid, n) for tid, n in (await session.execute(
        select(ArtifactVersion.task_id, func.count(ArtifactVersion.id)).group_by(ArtifactVersion.task_id)
    )).all())
    tasks = (await session.scalars(select(Task).order_by(Task.id.desc()).limit(200))).all()
    out = []
    for task in tasks:
        files = list_files(task.id)
        if not files and not counts.get(task.id):
            continue
        out.append({
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "workspace_dir": task.workspace_dir,
            "file_count": len(files),
            "version_count": counts.get(task.id, 0),
        })
    return out


@router.get("/tasks/{task_id}/files")
async def task_files(task_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    latest: dict[str, dict] = {}
    rows = (await session.scalars(
        select(ArtifactVersion).where(ArtifactVersion.task_id == task_id).order_by(ArtifactVersion.version.desc())
    )).all()
    per_path: dict[str, list[ArtifactVersion]] = defaultdict(list)
    for v in rows:
        per_path[v.path].append(v)
    files = []
    for item in list_files(task_id):
        versions = per_path.get(item["path"], [])
        files.append({
            **item,
            "versions": len(versions),
            "latest_version": versions[0].version if versions else 0,
            "latest_at": versions[0].created_at.isoformat() if versions else None,
        })
    # 有历史版本但当前已被删除的文件也列出来，便于找回
    current = {f["path"] for f in files}
    for path, versions in per_path.items():
        if path not in current:
            files.append({"path": path, "size": versions[0].size, "modified_at": None, "deleted": True,
                          "versions": len(versions), "latest_version": versions[0].version,
                          "latest_at": versions[0].created_at.isoformat()})
    files.sort(key=lambda f: f["path"])
    return {"task_id": task_id, "title": task.title, "files": files}


@router.post("/tasks/{task_id}/snapshot")
async def snapshot(task_id: int, body: SnapshotIn, request: Request,
                   user: User = Depends(require(Role.operator)), session: AsyncSession = Depends(get_session)):
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    created = await snapshot_task(task_id, paths=body.paths, note=body.note or "manual")
    await audit(session, "artifact.snapshot", user=user, target_type="task", target_id=task_id, request=request,
                detail={"created": created, "paths": (body.paths or [])[:20]})
    return {"created": created}


@router.get("/tasks/{task_id}/versions")
async def task_versions(task_id: int, path: str | None = None, _: User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    return await versions_for(session, task_id, path)


@router.get("/versions/{version_id}")
async def version_detail(version_id: int, _: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    try:
        v = await get_version(session, version_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    return _version_dict(v, with_content=True)


@router.get("/diff")
async def version_diff(a: int, b: int, _: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    try:
        left = await get_version(session, a)
        right = await get_version(session, b)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    return {"from": _version_dict(left), "to": _version_dict(right), "diff": diff_text(left, right)}


@router.post("/versions/{version_id}/restore")
async def restore(version_id: int, request: Request, user: User = Depends(require(Role.operator)),
                  session: AsyncSession = Depends(get_session)):
    try:
        result = await restore_version(session, version_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    await snapshot_task(result["task_id"], paths=[result["restored"]], note=f"restore v{result['version']}")
    await audit(session, "artifact.restore", user=user, target_type="task", target_id=result["task_id"],
                request=request, detail={"path": result["restored"], "version": result["version"]})
    return result
