"""Local agent transcript browsing (Claude Code JSONL)."""

import json

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


def test_list_sessions_reads_metadata(fake_claude):
    items = ls.list_sessions(root=fake_claude)
    assert len(items) == 2
    top = items[0]
    assert top["source"] == "claude"
    assert top["project"] == "D--work-demo"
    assert top["project_path"] == "D:/work/demo"
    assert top["title"] in {"帮我修复登录 bug", "看下部署脚本"}
    assert top["size_kb"] > 0


def test_list_sessions_query_filters(fake_claude):
    assert [s["id"] for s in ls.list_sessions(query="部署", root=fake_claude)] == ["bbbbbbbb-2222"]
    assert ls.list_sessions(query="不存在的词", root=fake_claude) == []


def test_get_session_builds_transcript(fake_claude):
    data = ls.get_session("D--work-demo", "aaaaaaaa-1111", root=fake_claude)
    kinds = [(e["role"], e["kind"]) for e in data["entries"]]
    assert ("user", "text") in kinds
    assert ("assistant", "text") in kinds
    assert ("assistant", "tool_use") in kinds
    assert ("tool", "tool_result") in kinds
    tool = next(e for e in data["entries"] if e["kind"] == "tool_use")
    assert tool["name"] == "Bash" and "pytest" in tool["text"]
    assert data["counts"]["assistant"] >= 1


def test_get_session_rejects_path_traversal(fake_claude):
    with pytest.raises(ValueError):
        ls.get_session("..", "x", root=fake_claude)
    with pytest.raises(ValueError):
        ls.get_session("D--work-demo", "../x", root=fake_claude)
    with pytest.raises(LookupError):
        ls.get_session("D--work-demo", "missing", root=fake_claude)


async def test_api_lists_and_returns_session(admin, client, fake_claude):
    listed = (await client.get("/api/local-sessions")).json()
    assert listed["source"] == "claude"
    assert len(listed["items"]) == 2

    sid = listed["items"][0]["id"]
    project = listed["items"][0]["project"]
    detail = (await client.get(f"/api/local-sessions/claude/{project}/{sid}")).json()
    assert detail["entries"]

    # encoded dot-dot segments reach the route (a literal ../ gets normalised away by the client)
    assert (await client.get("/api/local-sessions/claude/%2e%2e/passwd")).status_code == 422
    assert (await client.get("/api/local-sessions/claude/D--work-demo/%2e%2e")).status_code == 422
    assert (await client.get("/api/local-sessions/opencode/x/y")).status_code == 422
