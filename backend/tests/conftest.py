import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
async def app_env(tmp_path, monkeypatch):
    """Fresh data dir + database per test."""
    monkeypatch.setenv("AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AGENTOPS_DATABASE_URL", raising=False)
    from app import config, db, security
    from app.services import task_runner

    config.get_settings.cache_clear()
    security._jwt_secret.cache_clear()
    security._fernet.cache_clear()
    db.init_engine()
    await db.create_all()
    from app.services import collab

    task_runner.runner.__init__()
    collab.orchestrator.__init__()
    config.get_settings().workspaces_dir.mkdir(parents=True, exist_ok=True)
    yield tmp_path
    await collab.orchestrator.shutdown()
    await task_runner.runner.shutdown()
    await db.get_engine().dispose()


@pytest.fixture
async def client(app_env):
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin(client):
    r = await client.post("/api/auth/setup", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return r.json()["user"]


FIXTURES = Path(__file__).parent / "fixtures"
REAL = os.environ.get("AGENTOPS_REAL_AGENTS") == "1"
