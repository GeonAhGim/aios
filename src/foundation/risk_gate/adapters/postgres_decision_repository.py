"""`risk_decision` WORM 쓰기·읽기 — R-24.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2 102행, §9 R-24 (선행 R-02
6abb0dc `src/core/risk/decision.py`).

`RiskDecision`을 재정의하지 않고 R-02 계약을 그대로 저장·복원한다. 결정
영속화 유스케이스(감사·이벤트 발행)는 R-25(`risk_decision_recorder.py`)
소관이라 여기서는 순수 CRUD(C·R만, U/D는 WORM이라 없음)만 다룬다.

`inputs_snapshot`은 원값 그대로 저장한다 — 로그·예외 메시지에 절대 남기지
않는 것(§7)은 이 모듈이 아니라 호출자(recorder·로거)의 책임이다.

`risk_decision` 테이블에는 `evidence_ref` 컬럼이 없다(마이그레이션
`b8d5f2a1c3e4` docstring 참고 — 사후에 채워지는 감사 참조라 WORM 원장에
포함하지 않는다). `get()`은 항상 `evidence_ref=None`으로 복원한다.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome, RuleResult
from src.data.models.serialization import DecimalSafeEncoder


def _rule_result_to_json(result: RuleResult) -> dict[str, Any]:
    return {
        "rule_id": result.rule_id,
        "outcome": result.outcome.value,
        "reason_code": result.reason_code,
        "observed": str(result.observed) if result.observed is not None else None,
        "limit": str(result.limit) if result.limit is not None else None,
        "unit": result.unit,
        "missing_fields": list(result.missing_fields),
    }


def _rule_result_from_json(data: dict[str, Any]) -> RuleResult:
    return RuleResult(
        rule_id=data["rule_id"],
        outcome=RiskOutcome(data["outcome"]),
        reason_code=data["reason_code"],
        observed=Decimal(data["observed"]) if data["observed"] is not None else None,
        limit=Decimal(data["limit"]) if data["limit"] is not None else None,
        unit=data["unit"],
        missing_fields=tuple(data.get("missing_fields", ())),
    )


def _row_to_decision(row: asyncpg.Record) -> tuple[RiskDecision, dict[str, Any]]:
    decision = RiskDecision(
        decision_id=row["decision_id"],
        gate_kind=GateKind(row["gate_kind"]),
        tenant_id=row["tenant_id"],
        execution_ref=row["execution_ref"],
        subject_fingerprint=row["subject_fingerprint"],
        outcome=RiskOutcome(row["outcome"]),
        reason_codes=tuple(row["reason_codes"]),
        obligations=tuple(row["obligations"]),
        rule_results=tuple(
            _rule_result_from_json(item) for item in json.loads(row["rule_results"])
        ),
        rule_version=row["rule_version"],
        rule_hash=row["rule_hash"],
        engine_version=row["engine_version"],
        inputs_hash=row["inputs_hash"],
        input_refs=tuple(row["input_refs"]),
        evaluated_at=row["evaluated_at"],
        expires_at=row["expires_at"],
        trace_id=row["trace_id"],
        evidence_ref=None,
        latency_us=row["latency_us"],
    )
    inputs_snapshot: dict[str, Any] = json.loads(row["inputs_snapshot"])
    return decision, inputs_snapshot


class PostgresDecisionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(self, decision: RiskDecision, inputs_snapshot: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO risk_decision "
                "(decision_id, tenant_id, gate_kind, execution_ref, subject_fingerprint, "
                " outcome, reason_codes, obligations, rule_results, rule_version, rule_hash, "
                " engine_version, inputs_hash, inputs_snapshot, input_refs, trace_id, "
                " evaluated_at, expires_at, latency_us) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, "
                " $14::jsonb, $15, $16, $17, $18, $19)",
                decision.decision_id,
                decision.tenant_id,
                decision.gate_kind.value,
                decision.execution_ref,
                decision.subject_fingerprint,
                decision.outcome.value,
                list(decision.reason_codes),
                list(decision.obligations),
                json.dumps(
                    [_rule_result_to_json(item) for item in decision.rule_results],
                    cls=DecimalSafeEncoder,
                ),
                decision.rule_version,
                decision.rule_hash,
                decision.engine_version,
                decision.inputs_hash,
                json.dumps(inputs_snapshot, cls=DecimalSafeEncoder),
                list(decision.input_refs),
                decision.trace_id,
                decision.evaluated_at,
                decision.expires_at,
                decision.latency_us,
            )

    async def get(self, decision_id: UUID) -> tuple[RiskDecision, dict[str, Any]] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM risk_decision WHERE decision_id = $1", decision_id
            )
        return _row_to_decision(row) if row is not None else None

    async def get_for_tenant(
        self, decision_id: UUID, tenant_id: UUID
    ) -> tuple[RiskDecision, dict[str, Any]] | None:
        """I8 tenant 스코프 조회 — `fenced_submit`이 호출자 `GateDecision`을
        믿지 않고 F0·execution_ref·intent를 WORM에서 재조회할 때 쓴다
        (task-1532). 다른 tenant의 결정은 존재해도 `None`이다(존재 여부조차
        새지 않는다)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM risk_decision WHERE decision_id = $1 AND tenant_id = $2",
                decision_id,
                tenant_id,
            )
        return _row_to_decision(row) if row is not None else None

    async def list_recent(self, tenant_id: UUID, limit: int) -> tuple[RiskDecision, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM risk_decision WHERE tenant_id = $1 "
                "ORDER BY evaluated_at DESC LIMIT $2",
                tenant_id,
                limit,
            )
        return tuple(_row_to_decision(row)[0] for row in rows)


__all__ = ["PostgresDecisionRepository"]
