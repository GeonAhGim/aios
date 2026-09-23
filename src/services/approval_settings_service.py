"""11.4 — Approval settings (ApprovalMode) management.

Spec: 기능설계문서_v1.20.md#FD-11.3, policy docs 4.9/4.10, #13 §13.1

mandatory_wait_seconds accepts no user input — the platform-enforced 60-second
floor (DB CHECK, #13 §13.2) is maintained as-is; users may only change
mode / second_approver_contact.

SOLO mode selection is the FD-15.3 matching-warnings hook ③ point — RISK_MATCHING.
Interpreting APPROVAL_MODE_RISK_LEVEL, SOLO maps to the "aggressive" tier and is
cross-referenced against the user's risk_profile (warning on mismatch; save rejected
without explicit consent).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.risk_matching import APPROVAL_MODE_RISK_LEVEL, check_mismatch

APPROVAL_MODES = ("SOLO", "DUAL")


class ApprovalSettingsError(Exception):
    """Save rejected by FD-11.3 rules — router converts to 400."""


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
