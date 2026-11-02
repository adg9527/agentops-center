import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import agents, auth, collab, system, tasks
from .config import PROJECT_DIR, get_settings
from .db import create_all
from .services.collab import orchestrator, recover_collab
from .services.task_runner import recover_interrupted, runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    await create_all()
    await recover_collab()
    await recover_interrupted()
    yield
    await orchestrator.shutdown()
    await runner.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="AgentOps Center", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (auth.router, auth.users_router, agents.router, tasks.router, collab.router, system.router):
        app.include_router(r)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    # Serve the built frontend (npm run build) from the same origin when present.
    dist = PROJECT_DIR / "frontend" / "dist"
    manifest = dist / ".vite" / "manifest.json"
    if manifest.is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        shell = _render_shell(json.loads(manifest.read_text(encoding="utf-8")))

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            return HTMLResponse(shell)

    return app


def _render_shell(manifest: dict) -> str:
    """Build index.html from Vite's manifest instead of reading dist/index.html.

    On this host a transparent file-encryption (DLP) agent encrypts .html files for non-whitelisted
    processes such as python.exe, while .json/.js stay readable; rendering the shell sidesteps that.
    """
    entry = next(v for v in manifest.values() if v.get("isEntry"))
    css = "".join(f'<link rel="stylesheet" href="/{c}">' for c in entry.get("css", []))
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>AgentOps Center</title>'
        "<style>html,body,#root{height:100%;margin:0;background:#0b0f17}</style>"
        f'{css}<script type="module" crossorigin src="/{entry["file"]}"></script></head>'
        '<body><div id="root"></div></body></html>'
    )


app = create_app()
