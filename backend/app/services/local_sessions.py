"""Read the agent CLIs' own session transcripts (currently Claude Code).

Claude Code keeps one JSONL transcript per session under
`~/.claude/projects/<slugified-cwd>/<session-id>.jsonl`. The platform's task
records only know what the adapter reported; these files are the full record the
local tool itself keeps, which is what "进去看进展" needs.

Read-only: nothing here writes to the agent's directories.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

_MAX_TITLE = 90
_HEAD_BYTES = 256 * 1024


def _iso(ts: str | None) -> str | None:
    return ts or None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return ""


def _read_head(path: Path) -> list[dict]:
    """Parse the first chunk of a transcript: enough for cwd, title and timing."""
    rows: list[dict] = []
    with path.open("rb") as fh:
        data = fh.read(_HEAD_BYTES)
    for line in data.splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(rows) >= 40:
            break
    return rows


def _session_meta(path: Path, root: Path) -> dict | None:
    rows = _read_head(path)
    if not rows:
        return None
    cwd = ""
    started = None
    title = ""
    summary = ""
    for row in rows:
        cwd = cwd or str(row.get("cwd") or "")
        if row.get("type") == "summary" and row.get("summary"):
            summary = str(row["summary"])
        ts = row.get("timestamp")
        if ts and not started:
            started = ts
        if not title and row.get("type") == "user":
            message = row.get("message") or {}
            text = _text_of(message.get("content")).strip()
            if text and not text.startswith("<"):
                title = " ".join(text.split())[:_MAX_TITLE]
    if not title:
        title = summary or path.stem
    stat = path.stat()
    return {
        "source": "claude",
        "id": path.stem,
        "project": path.parent.name,
        "project_path": cwd,
        "title": title,
        "summary": summary,
        "started_at": _iso(started),
        "last_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "size_kb": round(stat.st_size / 1024, 1),
    }


def list_sessions(*, limit: int = 60, query: str = "", root: Path | None = None) -> list[dict]:
    """Most recently updated Claude Code sessions, optionally filtered by text."""
    base = root or CLAUDE_PROJECTS
    if not base.is_dir():
        return []
    files = sorted(base.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    seen: set[str] = set()
    q = query.strip().lower()
    for path in files:
        if len(out) >= limit:
            break
        if path.stem in seen:
            continue
        meta = _session_meta(path, base)
        if not meta:
            continue
        if q and q not in meta["title"].lower() and q not in (meta["project_path"] or "").lower():
            continue
        seen.add(path.stem)
        out.append(meta)
    return out


def _transcript(path: Path) -> list[dict]:
    entries: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = row.get("type")
            ts = row.get("timestamp")
            message = row.get("message") or {}
            content = message.get("content")
            if kind == "summary":
                continue
            if kind == "user":
                text = _text_of(content).strip()
                if text:
                    entries.append({"role": "user", "kind": "text", "text": text, "at": ts})
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            result = block.get("content")
                            entries.append({
                                "role": "tool",
                                "kind": "tool_result",
                                "name": "result",
                                "text": _text_of(result) or (result if isinstance(result, str) else ""),
                                "error": bool(block.get("is_error")),
                                "at": ts,
                            })
            elif kind == "assistant":
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and block.get("text"):
                            entries.append({"role": "assistant", "kind": "text", "text": str(block["text"]), "at": ts,
                                            "model": message.get("model")})
                        elif block.get("type") == "tool_use":
                            entries.append({"role": "assistant", "kind": "tool_use", "name": str(block.get("name") or ""),
                                            "text": json.dumps(block.get("input") or {}, ensure_ascii=False)[:4000],
                                            "at": ts})
            if len(entries) >= 2000:
                break
    return entries


def get_session(project: str, session: str, root: Path | None = None) -> dict:
    """Full transcript for one session; `project`/`session` must be plain names."""
    base = root or CLAUDE_PROJECTS
    for value, label in ((project, "project"), (session, "session")):
        if not value or any(ch in value for ch in "\\/.."):
            raise ValueError(f"非法的 {label} 名称")
    path = base / project / f"{session}.jsonl"
    if not path.is_file():
        raise LookupError("会话文件不存在")
    meta = _session_meta(path, base) or {"source": "claude", "id": session, "project": project, "title": session}
    entries = _transcript(path)
    counts = {"user": 0, "assistant": 0, "tool": 0}
    for e in entries:
        counts[e["role"] if e["role"] in counts else "tool"] = counts.get(e["role"], 0) + 1
    return {**meta, "counts": counts, "entries": entries}
