"""Evaluation suites: CRUD + start/stop the scoring loop."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import EvalRun, EvalSuite, Role, User
from ..services.audit import audit
from ..services.eval import default_thresholds, run_suite, stop_suite
from .deps import current_user, require

router = APIRouter(prefix="/api", tags=["evals"])


def suite_dict(s: EvalSuite, last: EvalRun | None = None) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "agent_ids": s.agent_ids,
        "skill_ids": s.skill_ids,
        "prompt_ids": s.prompt_ids,
        "mcp_ids": s.mcp_ids,
        "cases": s.cases,
        "thresholds": s.thresholds or default_thresholds(),
        "max_rounds": s.max_rounds,
        "enabled": s.enabled,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "last_run": run_dict(last) if last else None,
    }


def run_dict(r: EvalRun) -> dict:
    return {
        "id": r.id,
        "suite_id": r.suite_id,
        "round": r.round,
        "status": r.status,
        "passed": r.passed,
        "metrics": r.metrics,
        "task_ids": r.task_ids,
        "hints": r.hints,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


class SuiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    agent_ids: list[int] = Field(min_length=1)
    skill_ids: list[int] = Field(default_factory=list)
    prompt_ids: list[int] = Field(default_factory=list)
    mcp_ids: list[int] = Field(default_factory=list)
    cases: list[dict] = Field(min_length=1)
    thresholds: dict = Field(default_factory=default_thresholds)
    max_rounds: int = Field(3, ge=1, le=20)
    enabled: bool = True


class SuitePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    agent_ids: list[int] | None = None
    skill_ids: list[int] | None = None
    prompt_ids: list[int] | None = None
    mcp_ids: list[int] | None = None
    cases: list[dict] | None = None
    thresholds: dict | None = None
    max_rounds: int | None = Field(None, ge=1, le=20)
    enabled: bool | None = None


@router.get("/evals")
async def list_suites(_: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(
        select(EvalSuite).where(EvalSuite.deleted.is_(False)).order_by(EvalSuite.id.desc())
    )).all()
    out = []
    for suite in rows:
        last = await session.scalar(
            select(EvalRun).where(EvalRun.suite_id == suite.id).order_by(EvalRun.id.desc()).limit(1)
        )
        out.append(suite_dict(suite, last))
    return out


@router.post("/evals")
async def create_suite(body: SuiteIn, request: Request, user: User = Depends(require(Role.operator)),
                       session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(func.count(EvalSuite.id)).where(EvalSuite.name == body.name,
                                                                  EvalSuite.deleted.is_(False))):
        raise HTTPException(409, f"名称已存在：{body.name}")
    suite = EvalSuite(**body.model_dump())
    session.add(suite)
    await session.flush()
    await audit(session, "eval.create", user=user, target_type="eval", target_id=suite.id, request=request,
                detail={"name": suite.name, "cases": len(suite.cases or [])})
    return suite_dict(suite)


async def _get_suite(session: AsyncSession, suite_id: int) -> EvalSuite:
    suite = await session.get(EvalSuite, suite_id)
    if not suite or suite.deleted:
        raise HTTPException(404, "评测套件不存在")
    return suite


@router.patch("/evals/{suite_id}")
async def update_suite(suite_id: int, body: SuitePatch, request: Request,
                       user: User = Depends(require(Role.operator)), session: AsyncSession = Depends(get_session)):
    suite = await _get_suite(session, suite_id)
    changed = body.model_dump(exclude_unset=True)
    for key, value in changed.items():
        setattr(suite, key, value)
    await audit(session, "eval.update", user=user, target_type="eval", target_id=suite.id, request=request,
                detail={"changed": sorted(changed)})
    return suite_dict(suite)


@router.delete("/evals/{suite_id}")
async def delete_suite(suite_id: int, request: Request, user: User = Depends(require(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    suite = await _get_suite(session, suite_id)
    suite.deleted = True
    await audit(session, "eval.delete", user=user, target_type="eval", target_id=suite.id, request=request,
                detail={"name": suite.name})
    return {"ok": True}


@router.post("/evals/{suite_id}/run")
async def start_suite(suite_id: int, request: Request, user: User = Depends(require(Role.operator)),
                      session: AsyncSession = Depends(get_session)):
    suite = await _get_suite(session, suite_id)
    if not suite.enabled:
        raise HTTPException(422, "套件已停用")
    if not suite.agent_ids:
        raise HTTPException(422, "至少选择一个 Agent")
    if not suite.cases:
        raise HTTPException(422, "至少一个用例")
    running = await session.scalar(
        select(func.count(EvalRun.id)).where(EvalRun.suite_id == suite_id, EvalRun.status == "running")
    )
    if running:
        raise HTTPException(409, "该套件正在评测中")
    await audit(session, "eval.run", user=user, target_type="eval", target_id=suite.id, request=request,
                detail={"rounds": suite.max_rounds})
    asyncio.create_task(run_suite(suite_id))
    return {"started": True, "suite_id": suite_id}


@router.post("/evals/{suite_id}/stop")
async def stop_eval(suite_id: int, _: User = Depends(require(Role.operator))):
    stop_suite(suite_id)
    return {"stopping": True}


@router.get("/evals/{suite_id}/runs")
async def list_runs(suite_id: int, limit: int = 20, _: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    await _get_suite(session, suite_id)
    rows = (await session.scalars(
        select(EvalRun).where(EvalRun.suite_id == suite_id).order_by(EvalRun.id.desc()).limit(min(limit, 100))
    )).all()
    return [run_dict(r) for r in rows]


@router.get("/evals/runs/{run_id}")
async def get_run(run_id: int, _: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    run = await session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "评测记录不存在")
    return {**run_dict(run), "detail": run.detail}
