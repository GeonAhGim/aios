"""RiskGateRepository의 asyncpg 구현.

Spec: AIOSproject 78번 §1/§3.

insert_safety_control()의 fence 증가는 105번 표준의 conditional_update가
아니라 단일 `UPDATE ... SET current_token = current_token + 1 ... RETURNING`
이다 — 낙관적 동시성(기대값과 다르면 실패)이 아니라 "누가 먼저 오든 항상
성공하고, 매번 유일하게 커지는 토큰을 받는다"는 단조증가 카운터가 필요하기
때문(78번 §3 "increments target fence token" — 여러 요청이 동시에 kill
switch를 걸어도 전부 성공해야 하고, 각자 서로 다른 토큰을 받아야 한다).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.risk_gate.domain.models import (
    FenceSnapshot,
    GateKind,
    RiskEvaluation,
    RiskOutcome,
    SafetyControl,
    SafetyControlState,
    SafetyScope,
)


def _row_to_control(row: asyncpg.Record) -> SafetyControl:
    return SafetyControl(
        id=row["id"],
        scope=SafetyScope(row["scope"]),
        scope_ref=row["scope_ref"],
        state=SafetyControlState(row["state"]),
        reason=row["reason"],
        actor_subject_id=row["actor_subject_id"],
        fence_token=row["fence_token"],
        created_at=row["created_at"],
        deactivated_at=row["deactivated_at"],
        idempotency_digest=row["idempotency_digest"],
    )


def _row_to_evaluation(row: asyncpg.Record) -> RiskEvaluation:
    return RiskEvaluation(
        id=row["id"],
        tenant_id=row["tenant_id"],
        gate_kind=GateKind(row["gate_kind"]),
        subject_fingerprint=row["subject_fingerprint"],
        outcome=RiskOutcome(row["outcome"]),
        reason_codes=tuple(row["reason_codes"]),
        obligations=tuple(row["obligations"]),
        rule_version=row["rule_version"],
        evaluated_at=row["evaluated_at"],
        expires_at=row["expires_at"],
        trace_id=row["trace_id"],
    )


def _fence_query_parts(
    pairs: tuple[tuple[SafetyScope, str], ...]
) -> tuple[str, list[object]]:
    row_values = ", ".join(f"(${i * 2 + 1}, ${i * 2 + 2})" for i in range(len(pairs)))
    flat_params: list[object] = []
    for scope, scope_ref in pairs:
        flat_params.extend([scope.value, scope_ref])
    return row_values, flat_params


async def _fetch_fence_snapshot(
    conn: asyncpg.Connection, pairs: tuple[tuple[SafetyScope, str], ...]
) -> FenceSnapshot:
    row_values, flat_params = _fence_query_parts(pairs)
    rows = await conn.fetch(
        "SELECT scope, scope_ref, current_token FROM safety_fence "
        f"WHERE (scope, scope_ref) IN ({row_values})",
        *flat_params,
    )
    found = {
        (SafetyScope(row["scope"]), row["scope_ref"]): row["current_token"] for row in rows
    }
    # 한 번도 activate된 적 없는 (scope, scope_ref)는 행이 없다 — 토큰
    # 0(기준선)으로 채운다.
    return FenceSnapshot(tokens={pair: found.get(pair, 0) for pair in pairs})


class PostgresRiskGateRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_active_controls(
        self,
        *,
        tenant_id: UUID,
        provider_code: str | None = None,
        include_all_providers: bool = False,
    ) -> tuple[SafetyControl, ...]:
        refs: list[tuple[str, str]] = [
            ("GLOBAL", ""),
            ("TENANT", str(tenant_id)),
            ("ACCOUNT", str(tenant_id)),
        ]
        if provider_code is not None:
            refs.append(("PROVIDER", provider_code))

        # asyncpg는 "튜플의 리스트"를 그대로 배열 파라미터로 바인딩하지
        # 못하므로(record[] 캐스팅이 드라이버 버전에 따라 불안정), 각 (scope,
        # scope_ref) 쌍을 개별 위치 파라미터로 풀어 OR로 잇는다 — 후보가
        # 최대 4개뿐이라 동적 SQL 없이도 충분하다.
        conditions = " OR ".join(
            f"(scope = ${i * 2 + 1} AND scope_ref = ${i * 2 + 2})" for i in range(len(refs))
        )
        flat_params: list[object] = []
        for scope, ref in refs:
            flat_params.extend([scope, ref])

        if include_all_providers:
            # 특정 provider_code로 좁히지 않고 PROVIDER 범위 전체를 포함
            # (#2026-09-02-27) — scope_ref는 파라미터로 바인딩할 필요 없는
            # 리터럴 비교라 OR로 그냥 덧붙인다.
            conditions = f"({conditions}) OR scope = 'PROVIDER'"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM safety_control WHERE state = 'ACTIVE' AND ({conditions})",
                *flat_params,
            )
        return tuple(_row_to_control(row) for row in rows)

    async def insert_safety_control(
        self,
        *,
        scope: SafetyScope,
        scope_ref: str,
        reason: str,
        actor_subject_id: UUID,
    ) -> SafetyControl:
        async with self._pool.acquire() as conn, conn.transaction():
            fence_row = await conn.fetchrow(
                "INSERT INTO safety_fence (scope, scope_ref, current_token) "
                "VALUES ($1, $2, 1) "
                "ON CONFLICT (scope, scope_ref) DO UPDATE SET "
                " current_token = safety_fence.current_token + 1 "
                "RETURNING current_token",
                scope.value,
                scope_ref,
            )
            fence_token = fence_row["current_token"]

            row = await conn.fetchrow(
                "INSERT INTO safety_control "
                "(scope, scope_ref, reason, actor_subject_id, fence_token) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                scope.value,
                scope_ref,
                reason,
                actor_subject_id,
                fence_token,
            )
        return _row_to_control(row)

    async def get_safety_control(self, control_id: UUID) -> SafetyControl | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM safety_control WHERE id = $1", control_id)
        return _row_to_control(row) if row is not None else None

    async def deactivate_safety_control(self, control_id: UUID) -> SafetyControl:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
                "WHERE id = $1 AND state = 'ACTIVE' RETURNING *",
                control_id,
            )
        if row is None:
            raise ConcurrencyConflictError(
                f"safety_control.id={control_id}: 이미 비활성 상태이거나 존재하지 않습니다."
            )
        return _row_to_control(row)

    async def insert_evaluation(self, evaluation: RiskEvaluation) -> RiskEvaluation:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO risk_evaluation "
                "(tenant_id, gate_kind, subject_fingerprint, outcome, reason_codes, "
                " obligations, rule_version, expires_at, trace_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *",
                evaluation.tenant_id,
                evaluation.gate_kind.value,
                evaluation.subject_fingerprint,
                evaluation.outcome.value,
                list(evaluation.reason_codes),
                list(evaluation.obligations),
                evaluation.rule_version,
                evaluation.expires_at,
                evaluation.trace_id,
            )
        return _row_to_evaluation(row)

    async def get_cached_evaluation(
        self, tenant_id: UUID, fingerprint: str
    ) -> RiskEvaluation | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM risk_evaluation WHERE tenant_id = $1 AND subject_fingerprint = $2 "
                "AND (expires_at IS NULL OR expires_at > now()) "
                "ORDER BY evaluated_at DESC LIMIT 1",
                tenant_id,
                fingerprint,
            )
        return _row_to_evaluation(row) if row is not None else None

    async def read_fences(
        self, pairs: tuple[tuple[SafetyScope, str], ...]
    ) -> FenceSnapshot:
        # 78번 §3.6 — row-constructor IN 리스트는 스칼라 파라미터만 바인딩
        # 하므로(배열 타입이 아니다) asyncpg의 튜플-리스트 바인딩 제약을
        # 피하면서도 단일 쿼리(1 round trip)로 여러 쌍을 조회할 수 있다.
        async with self._pool.acquire() as conn:
            return await _fetch_fence_snapshot(conn, pairs)

    async def read_fence_and_controls(
        self, pairs: tuple[tuple[SafetyScope, str], ...]
    ) -> tuple[FenceSnapshot, tuple[SafetyControl, ...]]:
        row_values, flat_params = _fence_query_parts(pairs)
        async with self._pool.acquire() as conn, conn.transaction(isolation="repeatable_read"):
            fence_snapshot = await _fetch_fence_snapshot(conn, pairs)
            control_rows = await conn.fetch(
                "SELECT * FROM safety_control WHERE state = 'ACTIVE' "
                f"AND (scope, scope_ref) IN ({row_values})",
                *flat_params,
            )
        return fence_snapshot, tuple(_row_to_control(row) for row in control_rows)

    async def read_safety_state(
        self, *, provider_code: str, symbol: str
    ) -> tuple[str | None, str | None]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT "
                "(SELECT circuit_breaker_level FROM system_safety_state WHERE id = 1) "
                " AS cb_level, "
                "(SELECT level FROM data_distrust_state WHERE exchange = $1 AND symbol = $2) "
                " AS distrust_level",
                provider_code,
                symbol,
            )
        if row is None:
            raise RuntimeError("read_safety_state: 스칼라 SELECT가 0행을 반환함(있을 수 없음)")
        cb_level: str | None = row["cb_level"]
        distrust_level: str | None = row["distrust_level"]
        return cb_level, distrust_level

    async def invalidate_evaluations(self, *, tenant_id: UUID | None) -> None:
        async with self._pool.acquire() as conn:
            if tenant_id is None:
                await conn.execute("DELETE FROM risk_evaluation")
            else:
                await conn.execute(
                    "DELETE FROM risk_evaluation WHERE tenant_id = $1", tenant_id
                )
