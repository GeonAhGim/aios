"""The policy_bundle/policy_decision half of MandateRepository.

Split out because `postgres_repository.py` exceeded the 300-line cap (P6)
— the mandate/revision approval flow and the policy-compile-artifact/
evaluation cache are different tables with different responsibilities, so
splitting along a mixin boundary is natural. `PostgresMandateRepository`
inherits this mixin to implement the entire `MandateRepository` Protocol as
one class.
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
    """Mixed into a class that has `self._pool: asyncpg.Pool`
    (`PostgresMandateRepository`)."""

    _pool: asyncpg.Pool

    async def insert_policy_bundle(self, bundle: PolicyBundle) -> PolicyBundle:
        """`policy_bundle`은 이제 전체 행 WORM이다(`c6a3d8f14b92`) — 동시 삽입
        경쟁에서 진 쪽을 위한 upsert도 그 행에 UPDATE를 실행할 수 없다.
        `DO NOTHING`으로 충돌을 흡수하고(트리거를 아예 건드리지 않음), 승자의
        행을 `RETURNING`이 아니라 별도 SELECT로 다시 읽는다 — 105번 §2.2
        "UNIQUE 제약이 단일 소유자를 보장" 패턴과 동일하게, 진 쪽은 항상
        승자가 쓴 행을 그대로 읽는다."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO policy_bundle (mandate_revision_id, compiler_version, rule_hash) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (mandate_revision_id) DO NOTHING "
                "RETURNING *",
                bundle.mandate_revision_id,
                bundle.compiler_version,
                bundle.rule_hash,
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM policy_bundle WHERE mandate_revision_id = $1",
                    bundle.mandate_revision_id,
                )
                assert row is not None
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
