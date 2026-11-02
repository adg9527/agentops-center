"""Local agent transcript browsing: Claude Code JSONL + OpenCode SQLite."""

import json
import sqlite3

import pytest

from app.services import local_sessions as ls


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    root = tmp_path / "claude-projects"
    project = root / "D--work-demo"
    _write(project / "aaaaaaaa-1111.jsonl", [
        {"type": "summary", "summary": "修复登录问题"},
        {"type": "user", "cwd": "D:/work/demo", "timestamp": "2026-11-02T10:00:00Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "帮我修复登录 bug"}]}},
        {"type": "assistant", "timestamp": "2026-11-02T10:00:05Z",
         "message": {"role": "assistant", "model": "claude-sonnet", "content": [
             {"type": "text", "text": "先看日志"},
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}},
         ]}},
        {"type": "user", "timestamp": "2026-11-02T10:00:09Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "2 passed"},
         ]}},
    ])
    _write(project / "bbbbbbbb-2222.jsonl", [
        {"type": "user", "cwd": "D:/work/demo", "timestamp": "2026-11-02T11:00:00Z",
         "message": {"role": "user", "content": "看下部署脚本"}},
    ])
    monkeypatch.setattr(ls, "CLAUDE_PROJECTS", root)
    return root


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table session (id text primary key, title text, directory text, time_created integer,
                              time_updated integer, cost real, model text, tokens_input integer,
                              tokens_output integer, time_archived integer);
        create table message (id text primary key, session_id text, time_created integer, data text);
        create table part (id text primary key, message_id text, session_id text, time_created integer, data text);
        """
    )
    con.execute("insert into session values (?,?,?,?,?,?,?,?,?,?)",
                ("ses_1", "量化资料收集", "D:/work/demo", 1_700_000_000_000, 1_700_000_100_000,
                 0.03, '{"id":"deepseek-v4.1-flash"}', 100, 200, None))
    con.execute("insert into message values (?,?,?,?)",
                ("msg_1", "ses_1", 1_700_000_010_000, json.dumps({"role": "user"})))
    con.execute("insert into message values (?,?,?,?)",
                ("msg_2", "ses_1", 1_700_000_020_000, json.dumps({"role": "assistant", "modelID": "deepseek"})))
    con.execute("insert into part values (?,?,?,?,?)",
                ("p1", "msg_1", "ses_1", 1_700_000_010_000, json.dumps({"type": "text", "text": "收集一下资料"})))
    con.execute("insert into part values (?,?,?,?,?)",
                ("p2", "msg_2", "ses_1", 1_700_000_020_000, json.dumps({"type": "text", "text": "好的"})))
    con.execute("insert into part values (?,?,?,?,?)",
                ("p3", "msg_2", "ses_1", 1_700_000_021_000,
                 json.dumps({"type": "tool", "tool": "bash", "callID": "c1",
                             "state": {"status": "completed", "input": {"command": "ls"}, "output": "ok"}})))
    con.commit()
    con.close()
    monkeypatch.setattr(ls, "OPENCODE_DB", db)
    return db


# ------------------------------------------------------------------ Claude Code


def test_list_sessions_reads_metadata(fake_claude):
    items = ls.list_sessions(source="claude", claude_root=fake_claude)
    assert len(items) == 2
    top = items[0]
    assert top["source"] == "claude"
    assert top["project"] == "D--work-demo"
    assert top["project_path"] == "D:/work/demo"
    assert top["size_kb"] > 0


def test_list_sessions_query_filters(fake_claude):
    assert [s["id"] for s in ls.list_sessions(source="claude", query="部署", claude_root=fake_claude)] == ["bbbbbbbb-2222"]
    assert ls.list_sessions(source="claude", query="不存在的词", claude_root=fake_claude) == []


def test_get_session_builds_transcript(fake_claude):
    data = ls.get_claude_session("D--work-demo", "aaaaaaaa-1111", root=fake_claude)
    kinds = [(e["role"], e["kind"]) for e in data["entries"]]
    assert ("user", "text") in kinds
    assert ("assistant", "tool_use") in kinds
    assert ("tool", "tool_result") in kinds
    tool = next(e for e in data["entries"] if e["kind"] == "tool_use")
    assert tool["name"] == "Bash" and "pytest" in tool["text"]


def test_get_session_rejects_path_traversal(fake_claude):
    with pytest.raises(ValueError):
        ls.get_claude_session("..", "x", root=fake_claude)
    with pytest.raises(LookupError):
        ls.get_claude_session("D--work-demo", "missing", root=fake_claude)


# --------------------------------------------------------------------- OpenCode


def test_opencode_list_and_transcript(fake_opencode):
    items = ls.list_sessions(source="opencode", opencode_db=fake_opencode)
    assert len(items) == 1
    assert items[0]["source"] == "opencode" and items[0]["id"] == "ses_1"
    assert items[0]["model"] == "deepseek-v4.1-flash"

    detail = ls.get_opencode_session("ses_1", root=fake_opencode)
    kinds = [(e["role"], e["kind"]) for e in detail["entries"]]
    assert ("user", "text") in kinds
    assert ("assistant", "tool_use") in kinds
    tool = next(e for e in detail["entries"] if e["kind"] == "tool_use")
    assert tool["name"] == "bash"
    result = next(e for e in detail["entries"] if e["kind"] == "tool_result")
    assert result["text"] == "ok" and result["error"] is False


def test_all_sources_merge_and_sort(fake_claude, fake_opencode):
    items = ls.list_sessions(source="all", claude_root=fake_claude, opencode_db=fake_opencode)
    assert {i["source"] for i in items} == {"claude", "opencode"}
    times = [i["last_at"] or "" for i in items]
    assert times == sorted(times, reverse=True)


# -------------------------------------------------------------------------- API


async def test_api_lists_and_returns_session(admin, client, fake_claude, fake_opencode):
    listed = (await client.get("/api/local-sessions?source=all")).json()
    assert listed["source"] == "all"
    assert {i["source"] for i in listed["items"]} == {"claude", "opencode"}
    assert all("tasks" in i for i in listed["items"])

    claude = next(i for i in listed["items"] if i["source"] == "claude")
    detail = (await client.get(f"/api/local-sessions/claude/{claude['project']}/{claude['id']}")).json()
    assert detail["entries"]

    oc = (await client.get("/api/local-sessions/opencode/ses_1")).json()
    assert oc["source"] == "opencode" and oc["entries"]

    assert (await client.get("/api/local-sessions/nope/xyz")).status_code == 422
    assert (await client.get("/api/local-sessions/claude/%2e%2e/passwd")).status_code == 422


async def test_api_links_tasks_by_workspace(admin, client, fake_claude, fake_opencode):
    """A task whose workspace equals the session directory is linked back."""
    from app.db import session_factory
    from app.models import Task
    from app.services.trace import new_trace_id

    async with session_factory()() as s:
        s.add(Task(project_id=1, title="量化资料收集", prompt="p", status="completed",
                   trace_id=new_trace_id(), workspace_dir="D:/work/demo"))
        await s.commit()

    listed = (await client.get("/api/local-sessions?source=all")).json()
    oc = next(i for i in listed["items"] if i["source"] == "opencode")
    assert [t["title"] for t in oc["tasks"]] == ["量化资料收集"]
