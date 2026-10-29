# loc-allow: comprehensive negative/failure-injection/replay/perf tests for LB-16 reconcile_provider
"""LB-16 `reconcile_account`/`ExchangeBalanceSource` 통합테스트 — 실 DB
(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-16.
DoD(task-726): FakeAdapter로 거래소 응답을 대역하되(#4 PM 정책, 실키 e2e
금지) 분류는 FND-08 `run_reconciliation`을 실제로 호출해 검증한다 —
`pos_snapshot`은 실 Postgres, `reconciliation_run/item/state`/`safety_control`/
`safety_fence`도 이미 존재하는 실 테이블에 실제로 쓴다(새 테이블 없음).
"""

from __future__ import annotations

import functools
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.observability.metric_names import POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.data.models.trading import AccountBalance
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.positions.adapters.exchange_balance_source import (
    ExchangeBalanceSource,
    UnknownConnectionError,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.reconcile_provider import reconcile_account
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.application.run_reconciliation import run_reconciliation
from src.foundation.reconciliation.contracts.v1 import Classification
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

# -- internal fixtures (kept here for backward compat; moved to _reconcile_provider_fixtures.py) --


class FakeAdapter:
    """`ExchangeAdapter.get_balance`만 흉내내는 대역 — 나머지 추상 메서드는
    이 리프가 호출하지 않는다."""

    def __init__(
        self, balances: list[AccountBalance] | None = None, error: Exception | None = None
    ) -> None:
        self._balances = balances or []
        self._error = error

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        if self._error is not None:
            raise self._error
        return self._balances


def _recon(pool):
    return functools.partial(
        run_reconciliation,
        PostgresReconciliationRepository(pool),
        PostgresConnectionRepository(pool),
        PostgresRiskGateRepository(pool),
    )


def _balance(asset: str, amount: Decimal) -> AccountBalance:
    return AccountBalance(exchange="bitget", asset=asset, total=amount, available=amount)


async def _open_position(
    pool, *, tenant_id, account_id, asset: str, quantity: Decimal
) -> PositionSnapshotView:
    position_key = str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="bitget",
            instrument_id=asset,
            strategy_id="default",
            execution_id="p1",
        )
    )
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("1"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.USDT,
        last_journal_seq=0,
        updated_at=datetime.now(UTC),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


# -- tests --


async def test_material_mismatch_bumps_metric(pool):
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, asset=asset, quantity=Decimal("10")
    )
    connection_id = uuid4()
    provider = ExchangeBalanceSource({connection_id: FakeAdapter([_balance(asset, Decimal("1"))])})

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
    assert registry.counter(POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL).samples() == {(): 1.0}


async def test_matching_balance_does_not_bump_metric(pool):
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, asset=asset, quantity=Decimal("10")
    )
    connection_id = uuid4()
    provider = ExchangeBalanceSource({connection_id: FakeAdapter([_balance(asset, Decimal("10"))])})

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

    assert result.aggregate_classification == Classification.HEALTHY
    assert registry.counter(POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL).samples() == {}


async def test_adapter_exception_propagates(pool):
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    connection_id = uuid4()
    provider = ExchangeBalanceSource({connection_id: FakeAdapter(error=ConnectionError("boom"))})

    with pytest.raises(ConnectionError):
        await reconcile_account(
            tenant_id,
            account_id,
            connection_id=connection_id,
            snapshots=PostgresSnapshotRepository(pool),
            provider=provider,
            recon=_recon(pool),
            pool=pool,
            registry=registry,
        )


async def test_one_account_failure_does_not_block_another_accounts_reconciliation(pool):
    """DoD #5 negative test — 한 계좌의 잔고 조회 실패가 다른 계좌 대사를
    막지 않는다(공유 상태 없음을 증명)."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)

    failing_account = await create_pos_account(pool, tenant_id, venue="bitget")
    failing_connection = uuid4()
    failing_provider = ExchangeBalanceSource(
        {failing_connection: FakeAdapter(error=ConnectionError("boom"))}
    )
    with pytest.raises(ConnectionError):
        await reconcile_account(
            tenant_id,
            failing_account,
            connection_id=failing_connection,
            snapshots=PostgresSnapshotRepository(pool),
            provider=failing_provider,
            recon=_recon(pool),
            pool=pool,
            registry=registry,
        )

    healthy_account = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=healthy_account, asset=asset, quantity=Decimal("5")
    )
    healthy_connection = uuid4()
    healthy_provider = ExchangeBalanceSource(
        {healthy_connection: FakeAdapter([_balance(asset, Decimal("5"))])}
    )

    result = await reconcile_account(
        tenant_id,
        healthy_account,
        connection_id=healthy_connection,
        snapshots=PostgresSnapshotRepository(pool),
        provider=healthy_provider,
        recon=_recon(pool),
        pool=pool,
        registry=registry,
    )

    assert result.aggregate_classification == Classification.HEALTHY


async def test_unknown_connection_id_raises_unknown_connection_error(pool):
    """negative #3 — `connection_id`가 `ExchangeBalanceSource`에 매핑되지 않은
    배선 오류는 빈 잔고로 대체되지 않고 `UnknownConnectionError`로
    fail-closed한다(adapters/exchange_balance_source.py 27-29행 계약).
    기존 negative 2건은 둘 다 `get_balance()` 내부 예외(ConnectionError)만
    다뤘고, 이 경로(어댑터 자체가 없는 배선 오류)는 미검증이었다."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    provider = ExchangeBalanceSource({})

    with pytest.raises(UnknownConnectionError):
        await reconcile_account(
            tenant_id,
            account_id,
            connection_id=uuid4(),
            snapshots=PostgresSnapshotRepository(pool),
            provider=provider,
            recon=_recon(pool),
            pool=pool,
            registry=registry,
        )


@pytest.mark.perf
async def test_reconcile_account_completes_within_cycle_safety_margin(pool, perf_budget):
    """수치 성능 단언 — spec §7 B "브레이크 표면화: 감지 -> 알림 < 2분(대사
    주기 60s + 처리)"의 60s 주기 예산 대비 넉넉한 안전마진(30s)으로 단일
    계좌 `reconcile_account()` 지연을 잰다. 공유 TEST_DATABASE_URL은 다른
    리프가 남긴 행으로 계속 자라 타이트한 절대 임계는 못 쓴다
    (task-920/1029, bf513da8와 동일 교훈이라 넉넉하게 잡는다).

    raw time.monotonic() → perf_budget.sample_async() 전환(task-11065).
    """
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, asset=asset, quantity=Decimal("3")
    )
    connection_id = uuid4()
    provider = ExchangeBalanceSource({connection_id: FakeAdapter([_balance(asset, Decimal("3"))])})

    sample = await perf_budget.sample_async(
        lambda: reconcile_account(
            tenant_id,
            account_id,
            connection_id=connection_id,
            snapshots=PostgresSnapshotRepository(pool),
            provider=provider,
            recon=_recon(pool),
            pool=pool,
            registry=registry,
        )
    )

    assert sample.result.aggregate_classification == Classification.HEALTHY
    assert sample.wall_ms < 30_000.0
