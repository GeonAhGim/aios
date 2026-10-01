"""15.2 — Risk profile save and reassessment (RiskProfileService).

Spec: 기능설계문서_v1.20.md#FD-15.2, DB schema #04, FD-17.2

Appends history rows to risk_profile_history rather than overwriting previous
values (same spirit as 4.6-A Memory version-control principle — for post-hoc
audit). Reassessment interval is Draft: 12 months.

FD-15.2 exception ("if re-suitability yields a worse risk grade, immediately
alert that it conflicts with the currently RUNNING grade") cannot be verified
in practice yet because FD-16 (execution controller, strategy_executions) does
not exist — save_assessment() only returns whether the grade worsened
+(is_higher_risk). Wiring the existing-execution comparison and alert dispatch
to this return value must be done when FD-16 is implemented.
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

REASSESSMENT_INTERVAL_DAYS = 365  # Draft — 12 months

_SEVERITY = {RISK_PROFILE_STABLE: 0, RISK_PROFILE_NEUTRAL: 1, RISK_PROFILE_AGGRESSIVE: 2}


class RiskProfileError(Exception):
    """FD-15.2 failure — VALIDATION_INVALID_FIELD(400)."""


class RiskProfileNotFoundError(RiskProfileError):
    """`GET /users/me/risk-profile` — suitability assessment not yet completed
    at the time of lookup — RESOURCE_NOT_FOUND(404). `get_current()` preserves
    the existing contract of returning `None` as-is (tests/integration/
    test_risk_profile_service.py, no changes), and the router (suitability.py)
    raises this exception directly when it receives `None`."""


class RiskProfileRecord(BaseModel):
    risk_profile: str
    assessed_at: datetime
    next_reassessment_due: datetime
    is_higher_risk_than_previous: bool


class RiskProfileService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_assessment(self, user_id: UUID, result: SuitabilityResult) -> RiskProfileRecord:
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT risk_profile FROM users WHERE user_id = $1", user_id
            )
            if existing is None:
                raise RiskProfileError("User not found.")
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
