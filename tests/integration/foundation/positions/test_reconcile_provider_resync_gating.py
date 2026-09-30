"""DEEPEN(task-9366) split — resync gating invariants.

Split out of `test_reconcile_provider_resync.py` (ADR-2026-09-10-C §7 file policy —
the source file crossed the 500-line LOC observation threshold after this DEEPEN
block was appended, task-9468 main_drift). No behavior change: same test bodies,
same shared helpers (imported from the split source instead of redefined).

reconcile_provider.py의 불변식("MATERIAL_MISMATCH가 아니면 resync 자체를 시도하지
않는다", "journal/clock 둘 다 있어야만 opt-in", "resync 내부 예외는 삼켜지지 않고
그대로 전파된다", spec docs/audits/AUDIT_2026-09-29_ledger_accounting.md F1(M) DoD
#1/#3)이 실제로 지켜지는지를 겨눈다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.observability.metric_names import (
    POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL,
    POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency
from src.foundation.positions.adapters.exchange_balance_source import ExchangeBalanceSource
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application import reconcile_provider as reconcile_provider_module
from src.foundation.positions.application.reconcile_provider import reconcile_account
from src.foundation.reconciliation.contracts.v1 import Classification
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, force_row_replace
from tests.integration.foundation.positions.test_reconcile_provider_resync import (
    FakeAdapter,
    _balance,
    _clock,
    _key,
    _open_with_fill,
    _recon,
)


async def _make_healthy_account(
    pool: asyncpg.Pool,
) -> tuple[UUID, UUID, UUID, ExchangeBalanceSource]:
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
    provider = ExchangeBalanceSource(
        cast(dict[UUID, Any], {connection_id: FakeAdapter([_balance(asset, Decimal("100"))])})
    )
    return tenant_id, account_id, connection_id, provider


async def test_resync_not_attempted_when_classification_is_not_material_mismatch(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative — provider/journal이 이미 일치(HEALTHY)하면 journal/clock을
    넘겨도 `rebuild_snapshot`은 아예 호출되지 않는다. resync는 MATERIAL_MISMATCH
    전용이라는 불변식을 어기면 이 테스트가 잡는다(monkeypatch stub이 호출되면
    바로 AssertionError를 던진다)."""

    async def _must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("HEALTHY 분류에서 rebuild_snapshot이 호출되면 안 됩니다")

    monkeypatch.setattr(reconcile_provider_module, "rebuild_snapshot", _must_not_be_called)

    registry = MetricsRegistry()
    tenant_id, account_id, connection_id, provider = await _make_healthy_account(pool)

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
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {}
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {}


async def test_resync_skipped_when_only_journal_provided_without_clock(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative — opt-in은 `journal`과 `clock`이 둘 다 있어야 한다
    (reconcile_provider.py 모듈독스트링 "Resync is opt-in via journal/clock").
    `journal`만 넘기고 `clock`을 빠뜨리면 부분 opt-in으로 취급해 resync를
    시도해서는 안 된다 — 기존 `test_resync_disabled_by_default_...`는 둘 다
    빠진 경우만 커버해 이 경계값은 비어 있었다."""

    async def _must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("journal만 있고 clock이 없으면 rebuild_snapshot이 호출되면 안 됩니다")

    monkeypatch.setattr(reconcile_provider_module, "rebuild_snapshot", _must_not_be_called)

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
        # clock 의도적으로 생략 — 부분 opt-in.
    )

    assert result.aggregate_classification == Classification.MATERIAL_MISMATCH
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {}
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {}


async def test_resync_skipped_when_only_clock_provided_without_journal(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative — 위 테스트의 대칭 경계값: `clock`만 있고 `journal`이 없어도
    resync는 시도되지 않는다."""

    async def _must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("clock만 있고 journal이 없으면 rebuild_snapshot이 호출되면 안 됩니다")

    monkeypatch.setattr(reconcile_provider_module, "rebuild_snapshot", _must_not_be_called)

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
        # journal 의도적으로 생략 — 부분 opt-in.
        clock=_clock,
    )

    assert result.aggregate_classification == Classification.MATERIAL_MISMATCH
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {}
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {}


async def test_resync_rebuild_snapshot_exception_propagates_uncaught(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입(failure-injection) — `rebuild_snapshot` 내부에서 진짜 예외가
    나면(예: 커넥션 유실) DoD #1/#3("예외를 삼키지 않는다") 그대로 전파되어야
    한다. 지금은 `still_mismatched` 판정 로직만 있어 resync 실패를 "재시도해도
    여전히 어긋남"으로만 다루는데, 그 경로에 도달하기 전 인프라 예외까지
    RESYNC_FAILURE로 둔갑시키면 진짜 장애(재시도해도 낫지 않는 정합성 차이)와
    구분이 안 된다 — 두 메트릭 모두 건드리지 않고 예외가 그대로 올라오는지
    실측한다."""

    async def _raise_connection_lost(*args: object, **kwargs: object) -> None:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(reconcile_provider_module, "rebuild_snapshot", _raise_connection_lost)

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

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await reconcile_account(
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

    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_SUCCESS_COUNT_TOTAL).samples() == {}
    assert registry.counter(POSITIONS_RECONCILIATION_RESYNC_FAILURE_COUNT_TOTAL).samples() == {}, (
        "인프라 예외가 RESYNC_FAILURE로 둔갑했습니다 — 삼켜지지 않고 그대로 전파돼야 합니다"
    )
    async with pool.acquire() as conn:
        quantity = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert quantity == Decimal("999"), "부분 재폴딩(고아 갱신)이 남지 않아야 한다"
