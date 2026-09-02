"""MandateRepository의 asyncpg 구현.

Spec: AIOSproject 75번 §2/§4, 105번(동시성 표준).

activate_revision()의 진짜 직렬화 지점은 portfolio_mandate.active_revision_id를
호출자가 게이트 판단 시점에 관찰한 값(expected_active_revision_id) 그대로에
조건부로 거는 것이다 — revision 행 하나만 조건부 갱신하면 서로 다른 두
PROPOSED revision이 동시에 ACTIVE로 전이할 수 있고, 트랜잭션 내부에서 그
기대값을 재조회(예: `SELECT ... FOR UPDATE` 후 다시 읽기)하면 락은 순서만
정할 뿐 두 번째 요청이 자기가 방금 다시 읽은 값을 기준으로 성공해버려 결국
둘 다 순차적으로 activate되는 실결함이 된다. `UPDATE ... WHERE
active_revision_id IS NOT DISTINCT FROM $expected` 자체가 행 잠금 + 커밋 후
재검사(EvalPlanQual)이므로 별도 FOR UPDATE는 불필요하다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyBundle,
    PolicyDecision,
    PolicyOutcome,
    PortfolioMandate,
)


def _row_to_mandate(row: asyncpg.Record) -> PortfolioMandate:
    return PortfolioMandate(
        id=row["id"],
        tenant_id=row["tenant_id"],
        subject_id=row["subject_id"],
        active_revision_id=row["active_revision_id"],
        created_at=row["created_at"],
    )


def _row_to_revision(row: asyncpg.Record) -> MandateRevision:
    return MandateRevision(
        id=row["id"],
        mandate_id=row["mandate_id"],
        revision_no=row["revision_no"],
        state=MandateRevisionState(row["state"]),
        max_total_exposure_pct=float(row["max_total_exposure_pct"]),
        max_single_instrument_pct=float(row["max_single_instrument_pct"]),
        min_cash_buffer_pct=float(row["min_cash_buffer_pct"]),
        max_daily_loss_pct=float(row["max_daily_loss_pct"]),
        allowed_autonomy=Autonomy(row["allowed_autonomy"]),
        forbidden_assets=tuple(row["forbidden_assets"]),
        revision_hash=row["revision_hash"],
        cooling_off_started_at=row["cooling_off_started_at"],
        created_at=row["created_at"],
        activated_at=row["activated_at"],
    )


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


class PostgresMandateRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_mandate(self, tenant_id: UUID) -> PortfolioMandate | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM portfolio_mandate WHERE tenant_id = $1", tenant_id
            )
        return _row_to_mandate(row) if row is not None else None

    async def get_or_create_mandate(self, tenant_id: UUID, subject_id: UUID) -> PortfolioMandate:
        existing = await self.get_mandate(tenant_id)
        if existing is not None:
            return existing
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO portfolio_mandate (tenant_id, subject_id) "
                    "VALUES ($1, $2) RETURNING *",
                    tenant_id,
                    subject_id,
                )
            except asyncpg.UniqueViolationError:
                # UNIQUE(tenant_id) 위반 — 동시에 두 요청이 최초 draft를
                # 만들려던 경합. 이긴 쪽이 만든 행을 그대로 반환한다(105번
                # §2.2 "스키마 UNIQUE 제약이 단일 소유자를 보장"과 동일 패턴).
                row = await conn.fetchrow(
                    "SELECT * FROM portfolio_mandate WHERE tenant_id = $1", tenant_id
                )
                assert row is not None
        return _row_to_mandate(row)

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mandate_revision WHERE id = $1", revision_id
            )
        return _row_to_revision(row) if row is not None else None

    async def get_active_revision(self, mandate_id: UUID) -> MandateRevision | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT r.* FROM mandate_revision r "
                "JOIN portfolio_mandate m ON m.active_revision_id = r.id "
                "WHERE m.id = $1",
                mandate_id,
            )
        return _row_to_revision(row) if row is not None else None

    async def list_revisions(self, mandate_id: UUID) -> list[MandateRevision]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mandate_revision WHERE mandate_id = $1 ORDER BY revision_no",
                mandate_id,
            )
        return [_row_to_revision(row) for row in rows]

    async def insert_draft_revision(
        self, *, mandate_id: UUID, revision_no: int, rules: MandateRevision
    ) -> MandateRevision:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO mandate_revision "
                "(mandate_id, revision_no, state, max_total_exposure_pct, "
                " max_single_instrument_pct, min_cash_buffer_pct, max_daily_loss_pct, "
                " allowed_autonomy, forbidden_assets, revision_hash, cooling_off_started_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING *",
                mandate_id,
                revision_no,
                rules.state.value,
                rules.max_total_exposure_pct,
                rules.max_single_instrument_pct,
                rules.min_cash_buffer_pct,
                rules.max_daily_loss_pct,
                rules.allowed_autonomy.value,
                list(rules.forbidden_assets),
                rules.revision_hash,
                rules.cooling_off_started_at,
            )
        return _row_to_revision(row)

    async def transition_revision_state(
        self,
        revision_id: UUID,
        *,
        expected_state: str,
        new_state: str,
        extra_set_values: dict[str, object] | None = None,
    ) -> MandateRevision:
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table="mandate_revision",
                id_column="id",
                id_value=revision_id,
                expected_state_column="state",
                expected_state_value=expected_state,
                set_values={"state": new_state, **(extra_set_values or {})},
            )
        return _row_to_revision(row)

    async def activate_revision(
        self,
        mandate_id: UUID,
        revision_id: UUID,
        *,
        expected_active_revision_id: UUID | None,
    ) -> MandateRevision:
        async with self._pool.acquire() as conn, conn.transaction():
            # 진짜 직렬화 지점(모듈 docstring 참조) — expected_active_revision_id를
            # 트랜잭션 내부에서 다시 읽지 않는다.
            try:
                await conditional_update(
                    conn,
                    table="portfolio_mandate",
                    id_column="id",
                    id_value=mandate_id,
                    expected_state_column="active_revision_id",
                    expected_state_value=expected_active_revision_id,
                    set_values={"active_revision_id": revision_id},
                )
            except ConcurrencyConflictError:
                exists = await conn.fetchval(
                    "SELECT 1 FROM portfolio_mandate WHERE id = $1", mandate_id
                )
                if exists is None:
                    raise LookupError(f"존재하지 않는 mandate입니다: {mandate_id}") from None
                raise

            if expected_active_revision_id is not None:
                await conditional_update(
                    conn,
                    table="mandate_revision",
                    id_column="id",
                    id_value=expected_active_revision_id,
                    expected_state_column="state",
                    expected_state_value=MandateRevisionState.ACTIVE.value,
                    set_values={"state": MandateRevisionState.SUPERSEDED.value},
                )

            # "PROPOSED/DRAFT가 아닌 걸 activate하려는" 애플리케이션 계층 버그는
            # 방어적으로 걸러 조용히 잘못된 상태를 만들지 않는다.
            activated_row = await conn.fetchrow(
                "UPDATE mandate_revision SET state = $2, activated_at = $3 "
                "WHERE id = $1 AND state IN ('DRAFT', 'PROPOSED') "
                "RETURNING *",
                revision_id,
                MandateRevisionState.ACTIVE.value,
                datetime.now(timezone.utc),
            )
            if activated_row is None:
                raise ConcurrencyConflictError(
                    f"mandate_revision.id={revision_id}: DRAFT/PROPOSED 상태가 아니라 "
                    "activate할 수 없습니다."
                )
        return _row_to_revision(activated_row)

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


__all__ = ["PostgresMandateRepository", "ConcurrencyConflictError"]
