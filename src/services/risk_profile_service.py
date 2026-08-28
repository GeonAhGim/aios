"""15.2 — 위험등급 저장 및 재평가 (RiskProfileService).

Spec: 기능설계문서_v1.20.md#FD-15.2, 04번 DB스키마, FD-17.2

이전 값을 덮어쓰지 않고 risk_profile_history에 이력행을 추가한다(4.6-A
Memory 버전관리 원칙과 동일 정신 — 사후 조사 대비). 재평가 주기는
Draft 12개월.

FD-15.2 예외상황("재응시로 등급이 나빠지면 RUNNING 실행 중 새 등급과
불일치하는 것에 즉시 경고")은 FD-16(실행 제어판, strategy_executions)이
아직 없어 실제로 확인할 대상이 없다 — save_assessment()는 등급이
나빠졌는지(is_higher_risk) 여부만 반환하고, 기존 실행 대조·경고 발송은
FD-16 착수 시 이 반환값을 소비하는 쪽에서 연결해야 한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.suitability_questionnaire import (
    RISK_PROFILE_AGGRESSIVE,
    RISK_PROFILE_NEUTRAL,
    RISK_PROFILE_STABLE,
    SuitabilityResult,
)

REASSESSMENT_INTERVAL_DAYS = 365  # Draft — 12개월

_SEVERITY = {RISK_PROFILE_STABLE: 0, RISK_PROFILE_NEUTRAL: 1, RISK_PROFILE_AGGRESSIVE: 2}


class RiskProfileError(Exception):
    """FD-15.2 실패 — 라우터가 400/404로 변환."""


class RiskProfileRecord(BaseModel):
    risk_profile: str
    assessed_at: datetime
    next_reassessment_due: datetime
    is_higher_risk_than_previous: bool


class RiskProfileService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_assessment(
        self, user_id: UUID, result: SuitabilityResult
    ) -> RiskProfileRecord:
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT risk_profile FROM users WHERE user_id = $1", user_id
            )
            if existing is None:
                raise RiskProfileError("존재하지 않는 사용자입니다.")
            previous_profile = existing["risk_profile"]

            is_higher_risk = (
                previous_profile is not None
                and _SEVERITY[result.risk_profile] > _SEVERITY[previous_profile]
            )

            now = datetime.now(timezone.utc)
            await conn.execute(
                "UPDATE users SET risk_profile = $2, risk_profile_assessed_at = $3 "
                "WHERE user_id = $1",
                user_id,
                result.risk_profile,
                now,
            )
            await conn.execute(
                "INSERT INTO risk_profile_history "
                "(user_id, risk_profile, assessment_answers, assessed_at) "
                "VALUES ($1, $2, $3::jsonb, $4)",
                user_id,
                result.risk_profile,
                result.answers.model_dump_json(),
                now,
            )

        return RiskProfileRecord(
            risk_profile=result.risk_profile,
            assessed_at=now,
            next_reassessment_due=now + timedelta(days=REASSESSMENT_INTERVAL_DAYS),
            is_higher_risk_than_previous=is_higher_risk,
        )

    async def get_current(self, user_id: UUID) -> RiskProfileRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT risk_profile, risk_profile_assessed_at FROM users WHERE user_id = $1",
                user_id,
            )
        if row is None or row["risk_profile"] is None:
            return None
        assessed_at = row["risk_profile_assessed_at"]
        return RiskProfileRecord(
            risk_profile=row["risk_profile"],
            assessed_at=assessed_at,
            next_reassessment_due=assessed_at + timedelta(days=REASSESSMENT_INTERVAL_DAYS),
            is_higher_risk_than_previous=False,
        )

    async def get_history(self, user_id: UUID) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT risk_profile, assessment_answers, assessed_at "
                "FROM risk_profile_history WHERE user_id = $1 ORDER BY assessed_at ASC",
                user_id,
            )
        return [dict(row) for row in rows]
