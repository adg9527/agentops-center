import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog, User

log = logging.getLogger("agentops.audit")


async def audit(
    session: AsyncSession,
    action: str,
    *,
    user: User | None = None,
    username: str = "",
    target_type: str = "",
    target_id: str | int = "",
    detail: dict | None = None,
    request: Request | None = None,
    commit: bool = True,
):
    """Record an operation. `detail` must never contain secret values."""
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else username,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        detail=detail or {},
        ip=(request.client.host if request and request.client else ""),
    )
    session.add(entry)
    log.info("audit action=%s user=%s target=%s:%s", action, entry.username, target_type, target_id)
    if commit:
        await session.commit()
