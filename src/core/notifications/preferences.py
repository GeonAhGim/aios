"""17.4 — 알림 설정 관리.

Spec: 기능설계문서_v1.20.md#FD-17.4

강제 채널 필드는 애초에 notification_preferences 테이블에 컬럼으로 없다
(04번 DB스키마, 이중 방어) — 여기서는 그 컬럼 화이트리스트만 신뢰하고,
그 외 요청 필드는 전부 거부 대상으로 보고한다(호출부인 API 레이어가 이
`rejected_fields`를 근거로 403을 함께 반환할지 결정, FD-17.4 원문: "요청
전체를 거부하지 않고 허용된 필드는 처리").
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import BaseModel

ALLOWED_PREFERENCE_FIELDS = (
    "marketplace_purchase_email",
    "verification_result_email",
    "risk_mismatch_email",
)
_DEFAULTS = {field: True for field in ALLOWED_PREFERENCE_FIELDS}


class PreferenceUpdateResult(BaseModel):
    applied: dict[str, bool]
    rejected_fields: list[str]


async def get_notification_preferences(pool: asyncpg.Pool, user_id: UUID) -> dict[str, bool]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {', '.join(ALLOWED_PREFERENCE_FIELDS)} "
            "FROM notification_preferences WHERE user_id = $1",
            user_id,
        )
    return dict(row) if row is not None else dict(_DEFAULTS)


async def update_notification_preferences(
    pool: asyncpg.Pool, user_id: UUID, changes: dict[str, bool]
) -> PreferenceUpdateResult:
    allowed = {k: v for k, v in changes.items() if k in ALLOWED_PREFERENCE_FIELDS}
    rejected = [k for k in changes if k not in ALLOWED_PREFERENCE_FIELDS]

    if allowed:
        columns = list(allowed.keys())
        values = [allowed[c] for c in columns]
        insert_cols = ", ".join(["user_id", *columns])
        placeholders = ", ".join(f"${i + 2}" for i in range(len(columns)))
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO notification_preferences ({insert_cols})
                VALUES ($1, {placeholders})
                ON CONFLICT (user_id) DO UPDATE SET {update_clause}
                """,
                user_id,
                *values,
            )

    applied = await get_notification_preferences(pool, user_id)
    return PreferenceUpdateResult(applied=applied, rejected_fields=rejected)
