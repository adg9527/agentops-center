"""Task artifact snapshots — a lightweight, per-file version history.

Every time a task finishes (or a user asks), the files a task produced are hashed
and stored as an immutable version. Versions are content-addressed by SHA so an
unchanged file never gets a duplicate entry, and any version can be previewed,
diffed and restored into the workspace.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_factory
from ..models import ArtifactVersion, Task

MAX_BYTES = 2 * 1024 * 1024
SKIP_NAMES = {"_task_prompt.md"}


def workspace_of(task_id: int) -> Path:
    return get_settings().workspaces_dir / f"task-{task_id}"


def _safe_join(root: Path, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("非法的文件路径")
    path = (root / rel).resolve()
    if root.resolve() not in path.parents and path != root.resolve():
        raise ValueError("文件不在任务目录内")
    return path


def list_files(task_id: int) -> list[dict]:
    root = workspace_of(task_id)
    if not root.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({
            "path": path.relative_to(root).as_posix(),
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
        })
    return out


def _read(path: Path) -> tuple[str, str, int]:
    """Return (content, encoding, size); big/binary files are base64 encoded."""
    raw = path.read_bytes()[:MAX_BYTES]
    try:
        # 统一成 LF，避免 Windows 的 CRLF 让同一份内容产生不同版本
        return raw.decode("utf-8").replace("\r\n", "\n"), "text", len(raw)
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode("ascii"), "base64", len(raw)


async def snapshot_task(task_id: int, *, paths: list[str] | None = None, note: str = "auto") -> int:
    """Record new versions for the task's files (only when the content changed)."""
    root = workspace_of(task_id)
    if not root.is_dir():
        return 0
    targets = list_files(task_id)
    if paths:
        wanted = {p.replace("\\", "/").lstrip("/") for p in paths}
        targets = [t for t in targets if t["path"] in wanted]

    created = 0
    async with session_factory()() as s:
        task = await s.get(Task, task_id)
        if not task:
            return 0
        for item in targets:
            path = _safe_join(root, item["path"])
            try:
                content, encoding, size = _read(path)
            except OSError:
                continue
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            last = await s.scalar(
                select(ArtifactVersion)
                .where(ArtifactVersion.task_id == task_id, ArtifactVersion.path == item["path"])
                .order_by(ArtifactVersion.version.desc())
                .limit(1)
            )
            if last and last.sha256 == digest:
                continue
            s.add(ArtifactVersion(
                task_id=task_id, path=item["path"], version=(last.version + 1) if last else 1,
                sha256=digest, size=size, encoding=encoding, content=content, note=note[:128],
            ))
            created += 1
        if created:
            await s.commit()
    return created


def _version_dict(v: ArtifactVersion, with_content: bool = False) -> dict:
    data = {
        "id": v.id,
        "task_id": v.task_id,
        "path": v.path,
        "version": v.version,
        "sha256": v.sha256[:12],
        "size": v.size,
        "encoding": v.encoding,
        "note": v.note,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
    if with_content:
        data["content"] = v.content
    return data


async def versions_for(session: AsyncSession, task_id: int, path: str | None = None) -> list[dict]:
    q = select(ArtifactVersion).where(ArtifactVersion.task_id == task_id)
    if path:
        q = q.where(ArtifactVersion.path == path)
    rows = (await session.scalars(q.order_by(ArtifactVersion.path, ArtifactVersion.version.desc()))).all()
    return [_version_dict(v) for v in rows]


async def get_version(session: AsyncSession, version_id: int) -> ArtifactVersion:
    v = await session.get(ArtifactVersion, version_id)
    if not v:
        raise LookupError("版本不存在")
    return v


def diff_text(a: ArtifactVersion, b: ArtifactVersion) -> str:
    if a.encoding != "text" or b.encoding != "text":
        return "（二进制文件无法逐行比较）"
    lines = difflib.unified_diff(
        a.content.splitlines(), b.content.splitlines(),
        fromfile=f"{a.path} v{a.version}", tofile=f"{b.path} v{b.version}", lineterm="",
    )
    return "\n".join(list(lines)[:400])


async def restore_version(session: AsyncSession, version_id: int) -> dict:
    """Write a historical version back into the task workspace (as a new version on next snapshot)."""
    v = await get_version(session, version_id)
    root = workspace_of(v.task_id)
    root.mkdir(parents=True, exist_ok=True)
    path = _safe_join(root, v.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if v.encoding == "base64":
        path.write_bytes(base64.b64decode(v.content))
    else:
        path.write_text(v.content, encoding="utf-8", newline="\n")
    return {"restored": v.path, "version": v.version, "task_id": v.task_id}
