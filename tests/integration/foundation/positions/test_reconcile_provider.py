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

import asyncio
import functools
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.observability.metric_names import POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.data.models.trading import AccountBalance
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.domain.models import (
    AccountConnection,
    CapabilityScope,
    ConnectionState,
)
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
        updated_at=datetime.now(timezone.utc),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


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
async def test_reconcile_account_completes_within_cycle_safety_margin(pool):
    """수치 성능 단언 — spec §7 B "브레이크 표면화: 감지 -> 알림 < 2분(대사
    주기 60s + 처리)"의 60s 주기 예산 대비 넉넉한 안전마진(30s)으로 단일
    계좌 `reconcile_account()` 지연을 잰다. 공유 TEST_DATABASE_URL은 다른
    리프가 남긴 행으로 계속 자라 타이트한 절대 임계는 못 쓴다
    (task-920/1029, bf513da8와 동일 교훈이라 넉넉하게 잡는다)."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, asset=asset, quantity=Decimal("3")
    )
    connection_id = uuid4()
    provider = ExchangeBalanceSource({connection_id: FakeAdapter([_balance(asset, Decimal("3"))])})

    started = time.monotonic()
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
    elapsed_s = time.monotonic() - started

    assert result.aggregate_classification == Classification.HEALTHY
    assert elapsed_s < 30.0


async def test_unrelated_connection_health_does_not_override_this_accounts_classification(pool):
    """게이트 적색 재현 — `reconcile_provider.py` 모듈 docstring(18-25행)이
    문서화한 설계 결정을 직접 재현한다: `recon()` 호출의 `connection_id`는
    항상 `None`으로 고정해 FND-05 connection health 게이트(80번 §2)를
    우회한다. 이 테스트는 실제 `account_connection`/`connection_health` 행을
    만들어 그 connection이 DEGRADED임에도 잔고가 일치하면 여전히 HEALTHY로
    판정됨을 증명한다 — 누군가 `connection_id=None` 고정을 실수로
    `connection_id=connection_id`로 되돌리는 회귀를 만들면(health가 DEGRADED
    이므로 FND-08이 개별 판정 이전에 전체를 PROVIDER_UNAVAILABLE로 덮어씀),
    이 테스트가 즉시 적색이 된다."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")

    connection_repo = PostgresConnectionRepository(pool)
    connection = await connection_repo.insert_pending_connection(
        AccountConnection(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_subject_id=tenant_id,
            provider_code="bitget",
            opaque_account_ref=f"acct-{uuid4().hex[:8]}",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=(CapabilityScope.READ_BALANCE,),
            revision=1,
        )
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO connection_health (connection_id, state, error_code) "
            "VALUES ($1, 'DEGRADED', 'SIMULATED_UNHEALTHY')",
            connection.id,
        )

    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, asset=asset, quantity=Decimal("7")
    )
    provider = ExchangeBalanceSource({connection.id: FakeAdapter([_balance(asset, Decimal("7"))])})

    result = await reconcile_account(
        tenant_id,
        account_id,
        connection_id=connection.id,
        snapshots=PostgresSnapshotRepository(pool),
        provider=provider,
        recon=_recon(pool),
        pool=pool,
        registry=registry,
    )

    assert result.aggregate_classification == Classification.HEALTHY


async def test_replay_of_identical_reconciliation_reuses_cached_run(pool):
    """replay 증명 — FND-08 `run_reconciliation`의 input_hash 캐시(동일
    target_ref+input이면 `get_run_by_input_hash`가 기존 run을 반환하고
    `insert_run_with_items`/`activate_safety_control`을 다시 태우지 않는다,
    run_reconciliation.py 92-95행)가 `reconcile_provider` 경유 호출에서도
    지켜짐을 실제 테이블 행 수로 증명한다: 동일 불일치를 2번 재전송해도
    `reconciliation_run`에는 행이 1개만 남는다(재전송마다 새 run·새
    safety_control을 중복 생성하지 않는다)."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, asset=asset, quantity=Decimal("10")
    )
    connection_id = uuid4()
    provider = ExchangeBalanceSource({connection_id: FakeAdapter([_balance(asset, Decimal("1"))])})

    async def _run():
        return await reconcile_account(
            tenant_id,
            account_id,
            connection_id=connection_id,
            snapshots=PostgresSnapshotRepository(pool),
            provider=provider,
            recon=_recon(pool),
            pool=pool,
            registry=registry,
        )

    first = await _run()
    second = await _run()

    assert first.id == second.id
    assert first.aggregate_classification == Classification.MATERIAL_MISMATCH

    async with pool.acquire() as conn:
        run_count = await conn.fetchval(
            "SELECT count(*) FROM reconciliation_run WHERE target_ref = $1", account_id
        )
    assert run_count == 1


async def test_concurrent_reconciliation_is_isolated_when_one_account_is_under_adversarial_failure(
    pool,
):
    """동시성 + 적대적 증명 — 기존 negative 테스트(DoD #5)는 순차 호출만
    다뤘다. 여기서는 `asyncio.gather`로 실제 동시 실행 경로를 태운다:
    계좌 3개(정상 1 + 불일치 1 + 조회마다 ConnectionError를 던지는 적대적
    실패 계좌 1)를 같은 pool/MetricsRegistry로 동시에 대사해도, 실패
    계좌의 예외가 다른 두 계좌의 결과를 오염시키거나 서로의 판정을
    뒤바꾸지 않음을 증명한다(공유 가변 상태 없음의 동시 실행 버전)."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)

    healthy_account = await create_pos_account(pool, tenant_id, venue="bitget")
    healthy_asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=healthy_account,
        asset=healthy_asset,
        quantity=Decimal("4"),
    )
    healthy_connection = uuid4()
    healthy_provider = ExchangeBalanceSource(
        {healthy_connection: FakeAdapter([_balance(healthy_asset, Decimal("4"))])}
    )

    mismatch_account = await create_pos_account(pool, tenant_id, venue="bitget")
    mismatch_asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=mismatch_account,
        asset=mismatch_asset,
        quantity=Decimal("10"),
    )
    mismatch_connection = uuid4()
    mismatch_provider = ExchangeBalanceSource(
        {mismatch_connection: FakeAdapter([_balance(mismatch_asset, Decimal("1"))])}
    )

    failing_account = await create_pos_account(pool, tenant_id, venue="bitget")
    failing_connection = uuid4()
    failing_provider = ExchangeBalanceSource(
        {failing_connection: FakeAdapter(error=ConnectionError("adversarial boom"))}
    )

    async def _run(account_id, connection_id, provider):
        return await reconcile_account(
            tenant_id,
            account_id,
            connection_id=connection_id,
            snapshots=PostgresSnapshotRepository(pool),
            provider=provider,
            recon=_recon(pool),
            pool=pool,
            registry=registry,
        )

    healthy_result, mismatch_result, failing_result = await asyncio.gather(
        _run(healthy_account, healthy_connection, healthy_provider),
        _run(mismatch_account, mismatch_connection, mismatch_provider),
        _run(failing_account, failing_connection, failing_provider),
        return_exceptions=True,
    )

    assert healthy_result.aggregate_classification == Classification.HEALTHY
    assert mismatch_result.aggregate_classification == Classification.MATERIAL_MISMATCH
    assert isinstance(failing_result, ConnectionError)


async def test_provider_returns_zero_balance_for_open_position_reports_mismatch(pool):
    """negative #4 — 거래소 잔고가 0인데 포지션 수량이 양수이면 MATERIAL_MISMATCH
    로 분류된다(잔고 "없음"과 잔고 "0"은 동일: 둘 다 drift가 크다).
    LB-6 불변식: internal(포지션 수량) ≠ provider(거래소 잔고) → mismatch."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    asset = f"COIN{uuid4().hex[:8]}"
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        asset=asset,
        quantity=Decimal("5"),
    )
    connection_id = uuid4()
    # 거래소는 이 자산에 잔고가 0이라고 보고
    provider = ExchangeBalanceSource({connection_id: FakeAdapter([_balance(asset, Decimal("0"))])})

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
    # 메트릭도 증가해야 함
    assert registry.counter(POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL).samples() == {(): 1.0}


async def test_provider_balance_raises_runtime_error_propagates(pool):
    """실패주입 #3 — `get_balance()` 가 `RuntimeError`(예: 거래소 내부 오류) 를
    던지면 `reconcile_account`가 이를 삼키지 않고 그대로 전파한다(fail-closed,
    doD #3). FakeAdapter가 아닌 실제 monkeypatch로 의존성 예외를 유도한다."""
    registry = MetricsRegistry()
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id, venue="bitget")
    connection_id = uuid4()
    # FakeAdapter 대신 실제 ExchangeBalanceSource._adapters.get_balance 를
    # monkeypatch로 예외를 던지게 한다.
    real_source = ExchangeBalanceSource({connection_id: FakeAdapter()})

    async def _broken_get_balance(*args, **kwargs):
        raise RuntimeError("exchange internal server error")

    with pytest.raises(RuntimeError, match="exchange internal server error"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                type(real_source._adapters[connection_id]),
                "get_balance",
                _broken_get_balance,
            )
            await reconcile_account(
                tenant_id,
                account_id,
                connection_id=connection_id,
                snapshots=PostgresSnapshotRepository(pool),
                provider=real_source,
                recon=_recon(pool),
                pool=pool,
                registry=registry,
            )
