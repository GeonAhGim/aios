"""18.3 — User lookup and status change (UserAdminService).

Spec: 기능설계문서_v1.20.md#FD-18.3, Document #15 §15.6, FD-11.1, FD-11.4

Transitions to DELETED/PENDING_DELETION are exclusive to the FD-11.4
withdrawal flow and cannot be set directly by operators — do not
create a forced-withdrawal path independent of the user's own consent.
Operators can only toggle between ACTIVE and SUSPENDED.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log

ADMIN_SETTABLE_STATUSES = ("ACTIVE", "SUSPENDED")


class UserAdminError(Exception):
    """FD-18.3 failure — router converts to 400/404."""


class UserAdminNotFoundError(UserAdminError):
    """QA task-1163 — target user does not exist. Must distinguish as
    RESOURCE_NOT_FOUND(404) so the frontend can tell it apart from 400(bad value)."""


class UserSummary(BaseModel):
    user_id: UUID
    email: str
    status: str
    created_at: datetime


class UserStatusChangeResult(BaseModel):
    user_id: UUID
    status: str
    changed_at: datetime


class UserAdminService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_users(self, email_search: str | None = None) -> list[UserSummary]:
        async with self._pool.acquire() as conn:
            if email_search:
                rows = await conn.fetch(
                    "SELECT user_id, email, status, created_at FROM users "
                    "WHERE email ILIKE $1 ORDER BY created_at DESC",
                    f"%{email_search}%",
                )
            else:
                rows = await conn.fetch(
                    "SELECT user_id, email, status, created_at FROM users "
                    "ORDER BY created_at DESC"
                )
        return [UserSummary(**dict(row)) for row in rows]

    async def change_status(
        self, user_id: UUID, new_status: str, *, admin_user_id: UUID
    ) -> UserStatusChangeResult:
        if new_status not in ADMIN_SETTABLE_STATUSES:
            raise UserAdminError(
                f"운영자는 {'/'.join(ADMIN_SETTABLE_STATUSES)}로만 상태를 바꿀 수 있습니다 "
                f"— DELETED/PENDING_DELETION은 FD-11.4 탈퇴 절차를 이용해주세요."
            )

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE users SET status = $2 WHERE user_id = $1 RETURNING user_id",
                user_id,
                new_status,
            )
            if row is None:
                raise UserAdminNotFoundError("존재하지 않는 사용자입니다.")
            # actor_agent is the operator who executed this change, not the target user themselves —
            # same principle as dispute_resolution_service.resolve().
            await record_audit_log(
                conn, actor_agent=str(admin_user_id), action_type="user.status_changed",
                user_id=user_id,
                decision_data={"new_status": new_status, "changed_by": str(admin_user_id)},
            )
        return UserStatusChangeResult(
            user_id=user_id, status=new_status, changed_at=datetime.now(timezone.utc)
        )
