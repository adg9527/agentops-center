from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Role, User
from ..security import decode_access_token

_bearer = HTTPBearer(auto_error=False)
_RANK = {Role.viewer.value: 0, Role.operator.value: 1, Role.admin.value: 2}


async def user_from_token(token: str | None, session: AsyncSession) -> User | None:
    payload = decode_access_token(token) if token else None
    if not payload:
        return None
    user = await session.get(User, int(payload["sub"]))
    return user if user and user.is_active else None


async def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await user_from_token(cred.credentials if cred else None, session)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或登录已过期")
    return user


def require(role: Role):
    async def _dep(user: User = Depends(current_user)) -> User:
        if _RANK.get(user.role, -1) < _RANK[role.value]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"需要 {role.value} 权限")
        return user

    return _dep
