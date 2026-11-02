from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Project, Role, User
from ..security import create_access_token, hash_password, verify_password
from ..services.audit import audit
from .deps import current_user, require

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class NewUser(Credentials):
    role: Role = Role.viewer


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None}


async def _user_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(User.id)))


@router.get("/setup-status")
async def setup_status(session: AsyncSession = Depends(get_session)):
    return {"needs_setup": await _user_count(session) == 0}


@router.post("/setup")
async def setup(body: Credentials, request: Request, session: AsyncSession = Depends(get_session)):
    """First-run: create the initial admin. Only allowed while no users exist."""
    if await _user_count(session) > 0:
        raise HTTPException(409, "系统已初始化")
    user = User(username=body.username, password_hash=hash_password(body.password), role=Role.admin.value)
    session.add(user)
    if not await session.scalar(select(func.count(Project.id))):
        session.add(Project(name="默认项目", description="系统初始化时创建"))
    await session.flush()
    await audit(session, "system.setup", user=user, target_type="user", target_id=user.id, request=request)
    return {"token": create_access_token(user.id, user.role), "user": user_dict(user)}


@router.post("/login")
async def login(body: Credentials, request: Request, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.username == body.username))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        await audit(session, "auth.login_failed", username=body.username, request=request)
        raise HTTPException(401, "用户名或密码错误")
    await audit(session, "auth.login", user=user, request=request)
    return {"token": create_access_token(user.id, user.role), "user": user_dict(user)}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_dict(user)


@users_router.get("")
async def list_users(_: User = Depends(require(Role.admin)), session: AsyncSession = Depends(get_session)):
    return [user_dict(u) for u in (await session.scalars(select(User).order_by(User.id))).all()]


@users_router.post("")
async def create_user(body: NewUser, request: Request, admin: User = Depends(require(Role.admin)),
                      session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "用户名已存在")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role.value)
    session.add(u)
    await session.flush()
    await audit(session, "user.create", user=admin, target_type="user", target_id=u.id,
                detail={"username": u.username, "role": u.role}, request=request)
    return user_dict(u)


class UserPatch(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


@users_router.patch("/{user_id}")
async def patch_user(user_id: int, body: UserPatch, request: Request, admin: User = Depends(require(Role.admin)),
                     session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "用户不存在")
    if u.id == admin.id:
        raise HTTPException(400, "不能修改自己的角色或状态")
    if body.role is not None:
        u.role = body.role.value
    if body.is_active is not None:
        u.is_active = body.is_active
    await audit(session, "user.update", user=admin, target_type="user", target_id=u.id,
                detail=body.model_dump(exclude_none=True, mode="json"), request=request)
    return user_dict(u)
