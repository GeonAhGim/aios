"""18.3 — 사용자 조회·상태변경 (UserAdminService).

Spec: 기능설계문서_v1.20.md#FD-18.3, 15번 문서 §15.6, FD-11.1, FD-11.4

DELETED/PENDING_DELETION 전이는 FD-11.4 탈퇴 흐름 전용이라 운영자가
직접 세팅할 수 없다 — 사용자 본인 의사와 무관한 강제탈퇴 경로를 만들지
않는다. 운영자는 ACTIVE↔SUSPENDED만 오갈 수 있다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from pydantic import BaseModel

ADMIN_SETTABLE_STATUSES = ("ACTIVE", "SUSPENDED")


class UserAdminError(Exception):
    """FD-18.3 실패 — 라우터가 400/404로 변환."""


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

    async def change_status(self, user_id: UUID, new_status: str) -> UserStatusChangeResult:
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
            raise UserAdminError("존재하지 않는 사용자입니다.")
        return UserStatusChangeResult(
            user_id=user_id, status=new_status, changed_at=datetime.now(timezone.utc)
        )
