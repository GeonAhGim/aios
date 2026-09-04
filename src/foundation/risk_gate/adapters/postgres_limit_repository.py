"""`risk_limit`/`risk_limit_breach`의 asyncpg 구현 — R-26.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4/§9 R-26, §6 표
"risk_limit upsert" 행(낙관적 잠금).

`upsert()`는 105번 표준의 `conditional_update`(UPDATE 전용)를 쓰지 않는다 —
§6 표가 명시한 형태는 `INSERT ... ON CONFLICT DO UPDATE ... WHERE
risk_limit.updated_at = $expected RETURNING`로, 행이 아직 없으면 그냥
INSERT하고 있으면 조건부 UPDATE하는 단일 문장이 필요해 `conditional_update`
(항상 UPDATE만 시도)로는 표현할 수 없다. `ON CONFLICT`의 충돌 대상 표현식은
마이그레이션 `c7e6a3b2d4f5`의 `ux_risk_limit_scope` 인덱스 표현식과 토씨
하나까지 같아야 Postgres가 같은 인덱스로 추론한다.

`RiskLimit.updated_at`은 호출자가 이 함수에 넘길 때는 "마지막으로 읽은 값"
(신규 생성이면 `None`)이고, 반환값에서는 DB가 실제로 기록한 새 값이다 —
`IS NOT DISTINCT FROM`으로 비교해 `None`(아직 아무 행도 없다고 믿는 최초
생성)과 실제 `NULL` 저장값을 동일하게 다룬다. 기대값이 실제와 다르면(다른
요청이 먼저 갱신) `DO UPDATE ... WHERE`가 걸려 아무 행도 갱신되지 않고
`RETURNING`이 빈 결과를 낸다 — 그걸 무조건 성공으로 위장하지 않고
`ConcurrencyConflictError`로 호출자에게 알린다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.risk_gate.domain.models import LimitMetric, LimitScope, RiskLimit

_NIL_TENANT = "00000000-0000-0000-0000-000000000000"
# migration c7e6a3b2d4f5의 ux_risk_limit_scope 인덱스 표현식과 동일해야 한다.
_CONFLICT_EXPR = f"COALESCE(tenant_id, '{_NIL_TENANT}'::uuid), scope, scope_ref, metric"


def _row_to_limit(row: asyncpg.Record) -> RiskLimit:
    return RiskLimit(
        id=row["id"],
        tenant_id=row["tenant_id"],
        scope=LimitScope(row["scope"]),
        scope_ref=row["scope_ref"],
        metric=LimitMetric(row["metric"]),
        limit_value=row["limit_value"],
        hard=row["hard"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        created_by=row["created_by"],
        approval_ref=row["approval_ref"],
        updated_at=row["updated_at"],
    )


class PostgresLimitRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_effective(
        self,
        tenant_id: UUID,
        *,
        provider_code: str | None = None,
        strategy_id: str | None = None,
        symbols: tuple[str, ...] | None = None,
    ) -> tuple[RiskLimit, ...]:
        """이 tenant 소유 행(`tenant_id = $1`) 또는 플랫폼 기본값
        (`tenant_id IS NULL`)만 후보에 들어간다 — 다른 tenant의 한도는 이
        `WHERE`절 자체가 걸러내므로 scope 매칭 로직과 무관하게 교차 테넌트
        누출이 0건이다. ACCOUNT/ASSET_CLASS 스코프는 이 시그니처에 대응하는
        식별자 인자가 없어(78번 §2.6 표가 요구하는 인자는 provider_code/
        strategy_id/symbols뿐) 이 tenant 범위 안의 모든 ACCOUNT/ASSET_CLASS
        행을 후보로 남긴다 — 실제 자산군/계좌별 세부 매칭은 호출자
        (R-27 exposure_snapshot, 이 리프 범위 밖)의 책임이다."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM risk_limit "
                "WHERE (tenant_id = $1 OR tenant_id IS NULL) "
                "AND (effective_from IS NULL OR effective_from <= now()) "
                "AND (effective_to IS NULL OR effective_to > now()) "
                "AND ("
                "  (scope = 'TENANT' AND scope_ref = $1::text)"
                "  OR (scope = 'ACCOUNT' AND scope_ref = $1::text)"
                "  OR (scope = 'PROVIDER' AND $2::text IS NOT NULL AND scope_ref = $2)"
                "  OR (scope = 'STRATEGY' AND $3::text IS NOT NULL AND scope_ref = $3)"
                "  OR (scope = 'SYMBOL' AND $4::text[] IS NOT NULL AND scope_ref = ANY($4))"
                "  OR scope = 'ASSET_CLASS'"
                ")",
                tenant_id,
                provider_code,
                strategy_id,
                list(symbols) if symbols is not None else None,
            )
        return tuple(_row_to_limit(row) for row in rows)

    async def upsert(self, limit: RiskLimit) -> RiskLimit:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO risk_limit "  # noqa: S608 — _CONFLICT_EXPR는 모듈 상수(위 docstring)
                "(id, tenant_id, scope, scope_ref, metric, limit_value, hard, "
                " effective_from, effective_to, created_by, approval_ref, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, now()) "
                f"ON CONFLICT ({_CONFLICT_EXPR}) DO UPDATE SET "
                " limit_value = EXCLUDED.limit_value, hard = EXCLUDED.hard, "
                " effective_from = EXCLUDED.effective_from, "
                " effective_to = EXCLUDED.effective_to, "
                " created_by = EXCLUDED.created_by, approval_ref = EXCLUDED.approval_ref, "
                " updated_at = now() "
                "WHERE risk_limit.updated_at IS NOT DISTINCT FROM $12 "
                "RETURNING *",
                limit.id,
                limit.tenant_id,
                limit.scope.value,
                limit.scope_ref,
                limit.metric.value,
                limit.limit_value,
                limit.hard,
                limit.effective_from,
                limit.effective_to,
                limit.created_by,
                limit.approval_ref,
                limit.updated_at,
            )
        if row is None:
            raise ConcurrencyConflictError(
                f"risk_limit(tenant={limit.tenant_id}, scope={limit.scope.value}, "
                f"scope_ref={limit.scope_ref}, metric={limit.metric.value}): "
                "다른 요청이 먼저 갱신했습니다 — 다시 조회 후 시도하세요."
            )
        return _row_to_limit(row)

    async def record_breach(
        self,
        *,
        limit_id: UUID,
        decision_id: UUID,
        observed: Decimal,
        limit_value: Decimal,
        severity: str,
        occurred_at: datetime,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO risk_limit_breach "
                "(limit_id, decision_id, observed, limit_value, severity, occurred_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                limit_id,
                decision_id,
                observed,
                limit_value,
                severity,
                occurred_at,
            )
        if row is None:  # INSERT ... RETURNING은 항상 1행을 내야 한다(방어적 가드)
            raise RuntimeError("risk_limit_breach insert가 id를 반환하지 않았습니다")
        return int(row["id"])


__all__ = ["PostgresLimitRepository"]
