"""Artifact versions: snapshot, history, diff, restore."""

import pytest

from app.adapters import ADAPTERS
from app.adapters.base import AgentAdapter, ConnectionResult, Level, RunResult
from app.config import get_settings
from app.services.artifacts import list_files, snapshot_task, versions_for
from app.db import session_factory


class WriteFileAdapter(AgentAdapter):
    """Writes one file into the task workspace, like a real coding agent would."""

    type = "test_artifact_writer"
    display_name = "Writer"
    platform = "test"
    transport = "cli"
    verified = True
    config_fields = []
    capabilities = {"send_task": Level.supported}
    content = "v1\n"

    async def check_connection(self, config, secrets):
        return ConnectionResult(True, "ok")

    async def run(self, req, emit, cancel):
        (req.workdir / "report.md").write_text(WriteFileAdapter.content, encoding="utf-8")
        return RunResult(ok=True, output="written", model="test")


@pytest.fixture(autouse=True)
def _register():
    ADAPTERS["test_artifact_writer"] = WriteFileAdapter()
    WriteFileAdapter.content = "v1\n"
    yield
    ADAPTERS.pop("test_artifact_writer", None)


async def _agent(client, name="写文件"):
    r = await client.post("/api/agents", json={"name": name, "adapter_type": "test_artifact_writer", "model": ""})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _task(client, agent_id, prompt="写报告"):
    r = await client.post("/api/tasks", json={"prompt": prompt, "agent_ids": [agent_id]})
    return r.json()["id"]


async def test_task_completion_snapshots_files(admin, client):
    agent_id = await _agent(client)
    task_id = await _task(client, agent_id)

    for _ in range(200):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["status"] in ("completed", "failed"):
            break
        import asyncio

        await asyncio.sleep(0.05)

    files = (await client.get(f"/api/artifacts/tasks/{task_id}/files")).json()
    assert [f["path"] for f in files["files"]] == ["report.md"]
    assert files["files"][0]["versions"] == 1

    tasks = (await client.get("/api/artifacts/tasks")).json()
    assert any(t["id"] == task_id and t["file_count"] == 1 for t in tasks)


async def test_versions_diff_and_restore(admin, client):
    agent_id = await _agent(client)
    task_id = await _task(client, agent_id)

    import asyncio

    for _ in range(200):
        if (await client.get(f"/api/tasks/{task_id}")).json()["status"] == "completed":
            break
        await asyncio.sleep(0.05)

    # edit the file, take a second snapshot
    ws = get_settings().workspaces_dir / f"task-{task_id}"
    (ws / "report.md").write_text("v1\nv2\n", encoding="utf-8")
    created = (await client.post(f"/api/artifacts/tasks/{task_id}/snapshot", json={"note": "second"})).json()
    assert created["created"] == 1

    versions = (await client.get(f"/api/artifacts/tasks/{task_id}/versions?path=report.md")).json()
    assert [v["version"] for v in versions] == [2, 1]

    diff = (await client.get(f"/api/artifacts/diff?a={versions[1]['id']}&b={versions[0]['id']}")).json()
    assert "+v2" in diff["diff"]

    # restore v1 -> file content back to v1 -> becomes v3 on snapshot
    assert (await client.post(f"/api/artifacts/versions/{versions[1]['id']}/restore")).status_code == 200
    assert (ws / "report.md").read_text(encoding="utf-8") == "v1\n"
    versions = (await client.get(f"/api/artifacts/tasks/{task_id}/versions?path=report.md")).json()
    assert [v["version"] for v in versions] == [3, 2, 1]

    detail = (await client.get(f"/api/artifacts/versions/{versions[0]['id']}")).json()
    assert detail["content"] == "v1\n"


async def test_snapshot_is_idempotent_for_unchanged_files(admin, client):
    agent_id = await _agent(client)
    task_id = await _task(client, agent_id)
    import asyncio

    for _ in range(200):
        if (await client.get(f"/api/tasks/{task_id}")).json()["status"] == "completed":
            break
        await asyncio.sleep(0.05)

    assert (await client.post(f"/api/artifacts/tasks/{task_id}/snapshot", json={})).json()["created"] == 0


async def test_artifacts_404s(admin, client):
    assert (await client.get("/api/artifacts/tasks/99999/files")).status_code == 404
    assert (await client.get("/api/artifacts/versions/99999")).status_code == 404


async def test_recent_eval_runs_endpoint(admin, client):
    assert (await client.get("/api/evals/recent-runs")).status_code == 200
