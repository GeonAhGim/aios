"""F1 (task-8808) — `reconcile_account` MATERIAL_MISMATCH self-healing resync.

Spec: docs/audits/AUDIT_2026-09-29_ledger_accounting.md F1(M).
DoD: 1) on MATERIAL_MISMATCH, attempt a narrow self-heal seeded by the provider
     mismatch (re-uses LB-13 `rebuild_snapshot`, which re-folds `pos_journal`
     and holds the `pos_journal` advisory lock per `position_key`)
     2) success/failure each recorded via a dedicated metric
     3) concurrent `reconcile_account` calls on the same `position_key` are
     serialized by the advisory lock, not corrupted by a race

Case 1 (`test_resync_heals_stale_snapshot_cache`) targets the realistic bug this
leaf exists for: `pos_snapshot.quantity` drifted away from what `pos_journal`
actually folds to (a caching bug, not a real exchange event) — the provider
balance agrees with the *journal* truth, so re-folding heals it.
Case 2 (`test_resync_failure_keeps_material_mismatch_for_manual_intervention`)
is a real external divergence: the journal itself doesn't contain the fill the
exchange reports, so re-folding cannot invent it — `MATERIAL_MISMATCH` must
survive untouched for the manual path.
Case 3 (`test_concurrent_resync_is_serialized_by_position_lock`) fires two
`reconcile_account` calls at the same `position_key` concurrently and checks
both finish HEALTHY with no lost update.

Resync gating invariants (MATERIAL_MISMATCH-only, journal+clock opt-in,
exception propagation) live in `test_reconcile_provider_resync_gating.py`,
split out under ADR-2026-09-10-C §7 when this file crossed the 500-line LOC
observation threshold (task-9468 main_drift).
"""

from __future__ import annotations

import asyncio
import functools
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg

from src.core.observability.metric_names import (
    POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL,
    POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import AccountBalance, OrderSide
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.exchange_balance_source import ExchangeBalanceSource
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.reconcile_provider import reconcile_account
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from src.foundation.reconciliation.contracts.v1 import Classification, ReconciliationRunView
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    create_pos_account,
    force_row_replace,
    open_position,
)

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _recon(pool: asyncpg.Pool) -> Any:
    return functools.partial(
        run_reconciliation,
        PostgresReconciliationRepository(pool),
        PostgresConnectionRepository(pool),
        PostgresRiskGateRepository(pool),
    )


def _balance(asset: str, amount: Decimal) -> AccountBalance:
    return AccountBalance(exchange="bitget", asset=asset, total=amount, available=amount)


class FakeAdapter:
    def __init__(self, balances: list[AccountBalance]) -> None:
        self._balances = balances

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        return self._balances


def _key(tenant_id: UUID, asset: str) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="bitget",
            instrument_id=asset,
            strategy_id="default",
            execution_id="p1",
        )
    )


async def _open_with_fill(
    pool: asyncpg.Pool, *, tenant_id: UUID, account_id: UUID, position_key: str, quantity: Decimal
) -> None:
    await open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        base_currency=Currency.USDT,
    )
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        await record_fill(
            conn,
            RecordFillCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=uuid4(),
                fill_seq=1,
                side=OrderSide.BUY,
                quantity=quantity,
                price=Money(amount=Decimal("1"), currency=Currency.USDT),
                fee=None,
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=journal,
            snapshots=snapshots,
            audit=audit,
            clock=_clock,
        )


async def test_resync_heals_stale_snapshot_cache(pool: asyncpg.Pool) -> None:
    """Case 1 — a corrupted `pos_snapshot` cache (drifted from the journal)
    disagrees with the provider; re-folding from the journal heals it and the
    account re-verifies HEALTHY."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    asset = f"COIN{uuid4().hex[:8]}"
    position_key = _key(tenant_id, asset)
    await _open_with_fill(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("100"),
    )
    # Corrupt the cached snapshot quantity away from the journal truth (100).
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("999"),
    )

    connection_id = uuid4()
    provider = ExchangeBalanceSource(
        cast(dict[UUID, Any], {connection_id: FakeAdapter([_balance(asset, Decimal("100"))])})
    )

    result = await reconcile_account(
        tenant_id,
        account_id,
        connection_id=connection_id,
        snapshots=PostgresSnapshotRepository(pool),
        provider=provider,
        recon=_recon(pool),
        pool=pool,
        registry=registry,
        journal=PostgresJournalRepository(pool),
        clock=_clock,
    )

    assert result.aggregate_classification == Classification.HEALTHY
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {
        (): 1.0
    }
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {}

    async with pool.acquire() as conn:
        quantity = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert quantity == Decimal("100"), "재동기화 후 내부 스냅샷은 저널이 접은 값이어야 한다"


async def test_resync_failure_keeps_material_mismatch_for_manual_intervention(
    pool: asyncpg.Pool,
) -> None:
    """Case 2 — the journal genuinely lacks the exchange's fill (a real break,
    not a cache bug). Re-folding cannot manufacture the missing entry, so the
    resync fails and the existing MATERIAL_MISMATCH / manual path survives."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    asset = f"COIN{uuid4().hex[:8]}"
    position_key = _key(tenant_id, asset)
    await _open_with_fill(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("100"),
    )

    connection_id = uuid4()
    # Provider reports 50 — the journal (truth) says 100, so no re-fold heals this.
    provider = ExchangeBalanceSource(
        cast(dict[UUID, Any], {connection_id: FakeAdapter([_balance(asset, Decimal("50"))])})
    )

    result = await reconcile_account(
        tenant_id,
        account_id,
        connection_id=connection_id,
        snapshots=PostgresSnapshotRepository(pool),
        provider=provider,
        recon=_recon(pool),
        pool=pool,
        registry=registry,
        journal=PostgresJournalRepository(pool),
        clock=_clock,
    )

    assert result.aggregate_classification == Classification.MATERIAL_MISMATCH
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {
        (): 1.0
    }
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {}

    async with pool.acquire() as conn:
        quantity = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert quantity == Decimal("100"), "저널 진실값 그대로 남아야 한다(날조 금지)"


async def test_resync_disabled_by_default_preserves_prior_behavior(pool: asyncpg.Pool) -> None:
    """Negative — omitting `journal`/`clock` (existing callers: scheduler.py,
    pre-F1 tests) must not attempt any resync; MATERIAL_MISMATCH is returned
    exactly as before this leaf, with neither resync metric touched."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    asset = f"COIN{uuid4().hex[:8]}"
    position_key = _key(tenant_id, asset)
    await _open_with_fill(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        quantity=Decimal("100"),
    )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("999"),
    )

    connection_id = uuid4()
    provider = ExchangeBalanceSource(
        cast(dict[UUID, Any], {connection_id: FakeAdapter([_balance(asset, Decimal("100"))])})
    )

    result = await reconcile_account(
        tenant_id,
        account_id,
        connection_id=connection_id,
        snapshots=PostgresSnapshotRepository(pool),
        provider=provider,
        recon=_recon(pool),
        pool=pool,
        registry=registry,
    )

    assert result.aggregate_classification == Classification.MATERIAL_MISMATCH
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {}
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {}


_CONCURRENCY_REPEAT = 100


async def test_concurrent_resync_is_serialized_by_position_lock(pool: asyncpg.Pool) -> None:
    """Case 3 — two concurrent `reconcile_account` calls against the same
    `position_key` must not corrupt each other: `rebuild_snapshot`'s
    `pos_journal` advisory lock serializes the writes, so both calls finish
    HEALTHY and the final snapshot is the journal truth, not a torn write.

    task-9086 QA finding: a single `gather` pass only proves the race is
    *not observed once*, not that the lock actually serializes it — repeat
    the race 100x with a fresh account/asset/position_key per iteration (so
    iterations cannot cross-contaminate each other's rows — reusing one
    account would leave every prior iteration's now-open position in view of
    `reconcile_account`'s account-wide scan, which reports PROVIDER_UNAVAILABLE
    for assets the current iteration's fake provider doesn't know about) and
    require 0 UniqueViolationError plus journal-truth convergence on every
    iteration.
    """
    tenant_id = await create_test_tenant(pool)

    for _ in range(_CONCURRENCY_REPEAT):
        registry_a = MetricsRegistry()
        registry_b = MetricsRegistry()
        account_id = await create_pos_account(
            pool, tenant_id, venue="bitget", base_currency=Currency.USDT
        )
        asset = f"COIN{uuid4().hex[:8]}"
        position_key = _key(tenant_id, asset)
        await _open_with_fill(
            pool,
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            quantity=Decimal("100"),
        )
        await force_row_replace(
            pool,
            table="pos_snapshot",
            id_column="position_key",
            id_value=position_key,
            quantity=Decimal("777"),
        )

        connection_id_a = uuid4()
        connection_id_b = uuid4()
        provider_a = ExchangeBalanceSource(
            cast(dict[UUID, Any], {connection_id_a: FakeAdapter([_balance(asset, Decimal("100"))])})
        )
        provider_b = ExchangeBalanceSource(
            cast(dict[UUID, Any], {connection_id_b: FakeAdapter([_balance(asset, Decimal("100"))])})
        )

        results = await asyncio.gather(
            reconcile_account(
                tenant_id,
                account_id,
                connection_id=connection_id_a,
                snapshots=PostgresSnapshotRepository(pool),
                provider=provider_a,
                recon=_recon(pool),
                pool=pool,
                registry=registry_a,
                journal=PostgresJournalRepository(pool),
                clock=_clock,
            ),
            reconcile_account(
                tenant_id,
                account_id,
                connection_id=connection_id_b,
                snapshots=PostgresSnapshotRepository(pool),
                provider=provider_b,
                recon=_recon(pool),
                pool=pool,
                registry=registry_b,
                journal=PostgresJournalRepository(pool),
                clock=_clock,
            ),
            return_exceptions=True,
        )

        for outcome in results:
            if isinstance(outcome, BaseException):
                raise outcome

        assert (
            isinstance(results[0], ReconciliationRunView)
            and results[0].aggregate_classification == Classification.HEALTHY
        )
        assert (
            isinstance(results[1], ReconciliationRunView)
            and results[1].aggregate_classification == Classification.HEALTHY
        )

        async with pool.acquire() as conn:
            quantity = await conn.fetchval(
                "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
            )
        assert quantity == Decimal("100"), "동시 재동기화 후에도 저널 진실값으로 수렴해야 한다"

