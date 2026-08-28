"""11.4 — 승인 설정(ApprovalMode) 관리.

Spec: 기능설계문서_v1.20.md#FD-11.3, 정책문서 4.9/4.10, 13번 §13.1

mandatory_wait_seconds는 사용자 입력을 받지 않는다 — 플랫폼이 강제하는
60초 하한(DB CHECK, 13번 §13.2)을 그대로 유지, 사용자는 mode/
second_approver_contact만 바꿀 수 있다.

SOLO 모드 선택은 FD-15.3 매칭경고 훅③ 지점이다 — RISK_MATCHING.
APPROVAL_MODE_RISK_LEVEL 해석대로 SOLO를 "공격형"에 대응시켜 사용자
risk_profile과 대조한다(불일치 시 경고, 명시적 동의 없이는 저장 거부).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.risk_matching import APPROVAL_MODE_RISK_LEVEL, check_mismatch

APPROVAL_MODES = ("SOLO", "DUAL")


class ApprovalSettingsError(Exception):
    """FD-11.3 저장 거부 — 라우터가 400으로 변환."""


class ApprovalSettings(BaseModel):
    user_id: UUID
    mode: str
    second_approver_contact: str | None
    mandatory_wait_seconds: int
    risk_warning: str | None = None


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
        self,
        user_id: UUID,
        *,
        mode: str,
        second_approver_contact: str | None = None,
        risk_warning_acknowledged: bool = False,
    ) -> ApprovalSettings:
        if mode not in APPROVAL_MODES:
            raise ApprovalSettingsError(f"알 수 없는 승인 모드: {mode}")
        if mode == "DUAL" and not second_approver_contact:
            raise ApprovalSettingsError(
                "DUAL 모드는 second_approver_contact가 반드시 필요합니다."
            )

        async with self._pool.acquire() as conn:
            user_risk_profile = await conn.fetchval(
                "SELECT risk_profile FROM users WHERE user_id = $1", user_id
            )
            risk_warning = None
            if user_risk_profile is not None:
                risk_warning = check_mismatch(
                    user_risk_profile, APPROVAL_MODE_RISK_LEVEL.get(mode)
                )
                if risk_warning is not None and not risk_warning_acknowledged:
                    raise ApprovalSettingsError(risk_warning)

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
        settings = ApprovalSettings(**dict(row))
        settings.risk_warning = risk_warning if risk_warning_acknowledged else None
        return settings
