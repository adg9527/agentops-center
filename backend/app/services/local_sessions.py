"""Read the agent CLIs' own session transcripts.

Two local stores are supported, both read-only:

* **Claude Code** — one JSONL per session under `~/.claude/projects/<slug>/<id>.jsonl`
* **OpenCode** — sessions/messages/parts in `~/.local/share/opencode/opencode.db` (SQLite)

These are the full records the tools keep themselves; the platform's task rows only
know what the adapter reported.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

_MAX_TITLE = 90
_HEAD_BYTES = 256 * 1024
SOURCES = {"claude": "Claude Code", "opencode": "OpenCode"}


def _iso(ts: str | None) -> str | None:
    return ts or None


def _from_ms(ms: int | None) -> str | None:
    return datetime.fromtimestamp((ms or 0) / 1000).isoformat() if ms else None


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


def normalize_path(path: str | None) -> str:
    return (path or "").replace("\\", "/").rstrip("/").lower()


# ------------------------------------------------------------------ Claude Code


def _read_head(path: Path) -> list[dict]:
    """Parse the first chunk of a transcript.

    The chunk can end in the middle of a UTF-8 sequence (the CLI is appending while
    we read), so decode with replacement and drop the trailing partial line.
    """
    rows: list[dict] = []
    try:
        with path.open("rb") as fh:
            data = fh.read(_HEAD_BYTES)
    except OSError:
        return rows
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if len(lines) > 1:
        lines = lines[:-1]  # last line may be half-written
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:  # malformed JSON and undecodable bytes both land here
            continue
        if len(rows) >= 40:
            break
    return rows


def _claude_meta(path: Path) -> dict | None:
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
        "cost": None,
    }


def _claude_transcript(path: Path) -> list[dict]:
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
                                "role": "tool", "kind": "tool_result", "name": "result",
                                "text": _text_of(result) or (result if isinstance(result, str) else ""),
                                "error": bool(block.get("is_error")), "at": ts,
                            })
            elif kind == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        entries.append({"role": "assistant", "kind": "text", "text": str(block["text"]), "at": ts,
                                        "model": message.get("model")})
                    elif block.get("type") == "tool_use":
                        entries.append({"role": "assistant", "kind": "tool_use", "name": str(block.get("name") or ""),
                                        "text": json.dumps(block.get("input") or {}, ensure_ascii=False)[:4000], "at": ts})
            if len(entries) >= 2000:
                break
    return entries


# --------------------------------------------------------------------- OpenCode


def _oc_connect(root: Path | None = None) -> sqlite3.Connection:
    path = root or OPENCODE_DB
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def _oc_model(raw: str | None) -> str:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return ""
    return str(data.get("id") or "")


def _opencode_list(limit: int, query: str, root: Path | None) -> list[dict]:
    if not (root or OPENCODE_DB).is_file():
        return []
    q = query.strip().lower()
    try:
        con = _oc_connect(root)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "select id, title, directory, time_created, time_updated, cost, model, tokens_input, tokens_output "
            "from session where time_archived is null order by time_updated desc limit ?",
            (min(limit * 3, 400),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[dict] = []
    for row in rows:
        title = row["title"] or row["id"]
        directory = row["directory"] or ""
        if q and q not in title.lower() and q not in directory.lower():
            continue
        out.append({
            "source": "opencode",
            "id": row["id"],
            "project": Path(directory).name if directory else "opencode",
            "project_path": directory,
            "title": title[:_MAX_TITLE],
            "summary": "",
            "started_at": _from_ms(row["time_created"]),
            "last_at": _from_ms(row["time_updated"]),
            "size_kb": 0,
            "cost": row["cost"],
            "model": _oc_model(row["model"]),
            "tokens": (row["tokens_input"] or 0) + (row["tokens_output"] or 0),
        })
        if len(out) >= limit:
            break
    return out


def _opencode_transcript(session_id: str, root: Path | None) -> list[dict]:
    try:
        con = _oc_connect(root)
    except sqlite3.Error:
        return []
    try:
        messages = con.execute(
            "select id, data, time_created from message where session_id = ? order by time_created",
            (session_id,),
        ).fetchall()
        if not messages:
            return []
        ids = [m["id"] for m in messages]
        placeholders = ",".join("?" * len(ids))
        parts = con.execute(
            f"select message_id, data, time_created from part where message_id in ({placeholders}) order by time_created",
            ids,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    by_message: dict[str, list[dict]] = {}
    for part in parts:
        try:
            data = json.loads(part["data"])
        except json.JSONDecodeError:
            continue
        by_message.setdefault(part["message_id"], []).append(data)

    entries: list[dict] = []
    for message in messages:
        try:
            meta = json.loads(message["data"])
        except json.JSONDecodeError:
            meta = {}
        role = "user" if meta.get("role") == "user" else "assistant"
        ts = _from_ms(message["time_created"])
        for part in by_message.get(message["id"], []):
            ptype = part.get("type")
            if ptype == "text" and part.get("text"):
                entries.append({"role": role, "kind": "text", "text": str(part["text"]), "at": ts,
                                "model": meta.get("modelID")})
            elif ptype == "tool":
                state = part.get("state") or {}
                entries.append({
                    "role": "assistant", "kind": "tool_use", "name": str(part.get("tool") or "tool"),
                    "text": json.dumps(state.get("input") or {}, ensure_ascii=False)[:4000],
                    "at": ts,
                })
                if state.get("status") in ("completed", "error"):
                    entries.append({
                        "role": "tool", "kind": "tool_result", "name": str(part.get("tool") or "tool"),
                        "text": str(state.get("output") or state.get("error") or "")[:8000],
                        "error": state.get("status") == "error", "at": ts,
                    })
        if len(entries) >= 2000:
            break
    return entries


# ------------------------------------------------------------------------ public


def _claude_list(limit: int, query: str, root: Path | None) -> list[dict]:
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
        try:
            meta = _claude_meta(path)
        except OSError:
            continue
        if not meta:
            continue
        if q and q not in meta["title"].lower() and q not in (meta["project_path"] or "").lower():
            continue
        seen.add(path.stem)
        out.append(meta)
    return out


def list_sessions(*, source: str = "all", limit: int = 60, query: str = "",
                  claude_root: Path | None = None, opencode_db: Path | None = None) -> list[dict]:
    """Most recently updated sessions across the supported local tools."""
    items: list[dict] = []
    if source in ("all", "claude"):
        items += _claude_list(limit, query, claude_root)
    if source in ("all", "opencode"):
        items += _opencode_list(limit, query, opencode_db)
    items.sort(key=lambda s: s.get("last_at") or "", reverse=True)
    return items[:limit]


def get_claude_session(project: str, session: str, root: Path | None = None) -> dict:
    base = root or CLAUDE_PROJECTS
    for value, label in ((project, "project"), (session, "session")):
        if not value or any(ch in value for ch in "\\/.."):
            raise ValueError(f"非法的 {label} 名称")
    path = base / project / f"{session}.jsonl"
    if not path.is_file():
        raise LookupError("会话文件不存在")
    meta = _claude_meta(path) or {"source": "claude", "id": session, "project": project, "title": session,
                                  "project_path": "", "last_at": None}
    entries = _claude_transcript(path)
    return {**meta, "counts": _counts(entries), "entries": entries}


def get_opencode_session(session_id: str, root: Path | None = None) -> dict:
    if not session_id or any(ch in session_id for ch in "\\/.."):
        raise ValueError("非法的 session 名称")
    if not (root or OPENCODE_DB).is_file():
        raise LookupError("OpenCode 数据库不存在")
    con = None
    row = None
    try:
        con = _oc_connect(root)
        row = con.execute(
            "select id, title, directory, time_created, time_updated, cost, model from session where id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise LookupError(f"读取 OpenCode 数据库失败：{exc}") from None
    finally:
        if con is not None:
            con.close()
    if not row:
        raise LookupError("会话不存在")
    entries = _opencode_transcript(session_id, root)
    directory = row["directory"] or ""
    return {
        "source": "opencode",
        "id": row["id"],
        "project": Path(directory).name if directory else "opencode",
        "project_path": directory,
        "title": (row["title"] or row["id"])[:_MAX_TITLE],
        "summary": "",
        "started_at": _from_ms(row["time_created"]),
        "last_at": _from_ms(row["time_updated"]),
        "size_kb": 0,
        "cost": row["cost"],
        "model": _oc_model(row["model"]),
        "counts": _counts(entries),
        "entries": entries,
    }


def _counts(entries: list[dict]) -> dict:
    counts = {"user": 0, "assistant": 0, "tool": 0}
    for entry in entries:
        role = entry.get("role")
        counts[role if role in counts else "tool"] += 1
    return counts


# Backwards-compatible alias used by older tests/imports.
def get_session(project: str, session: str, root: Path | None = None) -> dict:
    return get_claude_session(project, session, root)
