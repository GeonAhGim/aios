"""MandateRepository의 policy_bundle/policy_decision 절반.

`postgres_repository.py`가 300줄 상한(P6)을 넘겨 분리했다 — mandate/revision
승인 흐름과 policy 컴파일 산출물·평가 캐시는 서로 다른 테이블·책임이라 믹스인
경계로 나누기 자연스럽다. `PostgresMandateRepository`가 이 믹스인을 상속해
`MandateRepository` Protocol 전체를 하나의 클래스로 구현한다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.mandates.domain.models import PolicyBundle, PolicyDecision, PolicyOutcome


def _row_to_bundle(row: asyncpg.Record) -> PolicyBundle:
    return PolicyBundle(
        id=row["id"],
        mandate_revision_id=row["mandate_revision_id"],
        compiler_version=row["compiler_version"],
        rule_hash=row["rule_hash"],
        created_at=row["created_at"],
    )


def _row_to_decision(row: asyncpg.Record) -> PolicyDecision:
    return PolicyDecision(
        id=row["id"],
        tenant_id=row["tenant_id"],
        bundle_id=row["bundle_id"],
        command_type=row["command_type"],
        command_fingerprint=row["command_fingerprint"],
        outcome=PolicyOutcome(row["outcome"]),
        reason_codes=tuple(row["reason_codes"]),
        obligations=tuple(row["obligations"]),
        evaluated_at=row["evaluated_at"],
        expires_at=row["expires_at"],
    )


class PostgresPolicyRepositoryMixin:
    """`self._pool: asyncpg.Pool`을 가진 클래스에 믹스인된다(`PostgresMandateRepository`)."""

    _pool: asyncpg.Pool

    async def insert_policy_bundle(self, bundle: PolicyBundle) -> PolicyBundle:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO policy_bundle (mandate_revision_id, compiler_version, rule_hash) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (mandate_revision_id) DO UPDATE SET mandate_revision_id = "
                "EXCLUDED.mandate_revision_id "
                "RETURNING *",
                bundle.mandate_revision_id,
                bundle.compiler_version,
                bundle.rule_hash,
            )
        return _row_to_bundle(row)

    async def get_bundle_for_revision(self, revision_id: UUID) -> PolicyBundle | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM policy_bundle WHERE mandate_revision_id = $1", revision_id
            )
        return _row_to_bundle(row) if row is not None else None

    async def insert_policy_decision(self, decision: PolicyDecision) -> PolicyDecision:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO policy_decision "
                "(tenant_id, bundle_id, command_type, command_fingerprint, outcome, "
                " reason_codes, obligations, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *",
                decision.tenant_id,
                decision.bundle_id,
                decision.command_type,
                decision.command_fingerprint,
                decision.outcome.value,
                list(decision.reason_codes),
                list(decision.obligations),
                decision.expires_at,
            )
        return _row_to_decision(row)

    async def get_cached_decision(
        self, tenant_id: UUID, command_fingerprint: str
    ) -> PolicyDecision | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM policy_decision WHERE tenant_id = $1 AND command_fingerprint = $2 "
                "AND (expires_at IS NULL OR expires_at > now()) "
                "ORDER BY evaluated_at DESC LIMIT 1",
                tenant_id,
                command_fingerprint,
            )
        return _row_to_decision(row) if row is not None else None
