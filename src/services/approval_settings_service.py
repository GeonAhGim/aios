"""11.4 — 승인 설정(ApprovalMode) 관리.

Spec: 기능설계문서_v1.20.md#FD-11.3, 정책문서 4.9/4.10, 13번 §13.1

mandatory_wait_seconds는 사용자 입력을 받지 않는다 — 플랫폼이 강제하는
60초 하한(DB CHECK, 13번 §13.2)을 그대로 유지, 사용자는 mode/
second_approver_contact만 바꿀 수 있다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import BaseModel

APPROVAL_MODES = ("SOLO", "DUAL")


class ApprovalSettingsError(Exception):
    """FD-11.3 저장 거부 — 라우터가 400으로 변환."""


class ApprovalSettings(BaseModel):
    user_id: UUID
    mode: str
    second_approver_contact: str | None
    mandatory_wait_seconds: int


class ApprovalSettingsService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, user_id: UUID) -> ApprovalSettings:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM user_approval_settings WHERE user_id = $1", user_id
            )
        if row is None:
            return ApprovalSettings(
                user_id=user_id,
                mode="SOLO",
                second_approver_contact=None,
                mandatory_wait_seconds=60,
            )
        return ApprovalSettings(**dict(row))

    async def update(
        self, user_id: UUID, *, mode: str, second_approver_contact: str | None = None
    ) -> ApprovalSettings:
        if mode not in APPROVAL_MODES:
            raise ApprovalSettingsError(f"알 수 없는 승인 모드: {mode}")
        if mode == "DUAL" and not second_approver_contact:
            raise ApprovalSettingsError(
                "DUAL 모드는 second_approver_contact가 반드시 필요합니다."
            )

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO user_approval_settings (user_id, mode, second_approver_contact)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE
                    SET mode = EXCLUDED.mode,
                        second_approver_contact = EXCLUDED.second_approver_contact,
                        updated_at = now()
                RETURNING *
                """,
                user_id,
                mode,
                second_approver_contact,
            )
        return ApprovalSettings(**dict(row))
