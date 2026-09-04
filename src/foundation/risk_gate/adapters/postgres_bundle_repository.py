"""RiskRuleBundleRepository의 asyncpg 구현 — `risk_rule_bundle`.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4/§9 R-22 (선행 R-15 9a1737a
`src.core.risk.policy_bundle`).

반환 타입은 스펙 표의 `RuleBundleRecord`(`domain/models.py`, 다른 리프 소관)
대신 R-15가 이미 이 테이블과 1:1로 만들어 둔
`src.core.risk.policy_bundle.RiskRuleBundle`을 그대로 쓴다 — 같은 필드
집합을 두 번 정의하지 않는다.

`transition()`은 전이 적법성(`is_valid_transition`, R-15)을 미리 검사하지
않는다 — 그건 호출자(R-23 application 계층)의 책임이고, 이 저장소는
`conditional_update`(105번 표준, [[src/core/db/conditional_write.py]])로
`expected_state` 불일치를 `ConcurrencyConflictError`(0행 갱신)로 드러내는
것까지만 한다. scope당 ACTIVE 1개(I6)는 `ux_bundle_active` partial unique가
커밋 시점에 강제한다 — 서로 다른 두 번들을 같은 scope로 동시에 ACTIVE
전이시키면 나중에 커밋하는 트랜잭션이 `UniqueViolationError`를 받는다.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.data.models.serialization import DecimalSafeEncoder


def _row_to_bundle(row: asyncpg.Record) -> RiskRuleBundle:
    return RiskRuleBundle(
        id=row["id"],
        scope=row["scope"],
        version=row["version"],
        rule_hash=row["rule_hash"],
        engine_version=row["engine_version"],
        policy_snapshot=json.loads(row["policy_snapshot"]),
        state=BundleState(row["state"]),
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        created_by=row["created_by"],
        approved_by=row["approved_by"],
        approval_ref=row["approval_ref"],
        approved_at=row["approved_at"],
        activated_at=row["activated_at"],
        retired_at=row["retired_at"],
    )


class PostgresBundleRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_active(self, scope: str) -> RiskRuleBundle | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM risk_rule_bundle WHERE scope = $1 AND state = 'ACTIVE'",
                scope,
            )
        return _row_to_bundle(row) if row is not None else None

    async def get_by_id(self, bundle_id: UUID) -> RiskRuleBundle | None:
        """R-23 승인 전(DRAFT/APPROVED) 번들 조회 — `get_active`는 ACTIVE만
        보므로 승인자=작성자 검사(호출자 책임)에는 이 메서드가 필요하다."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM risk_rule_bundle WHERE id = $1", bundle_id
            )
        return _row_to_bundle(row) if row is not None else None

    async def insert_draft(self, bundle: RiskRuleBundle) -> RiskRuleBundle:
        if bundle.state != BundleState.DRAFT:
            raise ValueError("insert_draft는 DRAFT 상태 번들만 받습니다")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO risk_rule_bundle "
                "(id, scope, version, rule_hash, engine_version, policy_snapshot, "
                " state, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8) RETURNING *",
                bundle.id,
                bundle.scope,
                bundle.version,
                bundle.rule_hash,
                bundle.engine_version,
                json.dumps(dict(bundle.policy_snapshot), cls=DecimalSafeEncoder),
                bundle.state.value,
                bundle.created_by,
            )
        return _row_to_bundle(row)

    async def transition(
        self,
        bundle_id: UUID,
        *,
        expected_state: BundleState,
        new_state: BundleState,
        **audit: Any,
    ) -> RiskRuleBundle:
        """`expected_state` 불일치(동시 전이 경합 포함)는 `ConcurrencyConflictError`로
        드러난다(0행 갱신을 성공으로 위장하지 않는다). `**audit`의 키는 호출자
        코드에 상수로 박힌 컬럼명이어야 한다(예: `approved_by=`, `approval_ref=`,
        `approved_at=`, `activated_at=`, `retired_at=`, `effective_from=`) —
        `conditional_update` 계약과 동일하게 사용자 입력을 컬럼명으로 받지 않는다.
        """
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table="risk_rule_bundle",
                id_column="id",
                id_value=bundle_id,
                expected_state_column="state",
                expected_state_value=expected_state.value,
                set_values={"state": new_state.value, **audit},
            )
        return _row_to_bundle(row)


__all__ = ["PostgresBundleRepository"]
