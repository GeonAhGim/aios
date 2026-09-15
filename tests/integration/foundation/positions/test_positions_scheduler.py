"""LB-17 `PositionsScheduler` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-17.
DoD(task-727): "스케줄 1주기 실행 후 게이지·스냅샷이 갱신됨을 실제로
단언한다" — sleep 기반 타이밍 대신 `asyncio.Event`로 결정론화한다
(`tests/unit/core/test_base_loop.py` 선례, task-409). 마크가격/FX는 이
리프의 관심사가 아니므로(LB-14가 이미 실DB로 검증) in-memory fake로
대역하고, `pos_snapshot`은 실제 Postgres를 쓴다.

DEEPEN(task-2983, docs/audit/DEPTH_LA_LB_LC.md#727): 원 리프가 negative
1건(마크 격리)만 증명했다 — 아래 네 테스트로 (1) reconcile 단계 격리
negative, (2) 마크 사이클과 동시 체결이 경합하는 실제 낙관적 동시성
충돌(적대적/동시성 증명 + negative #3), (3) `run_mark_cycle()`의 수치
성능 예산, (4) 계좌별 격리를 제거한 회귀가 기존 negative를 green→red로
뒤집는 게이트 적색 재현을 채운다.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.core.observability.metric_names import (
    POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL,
    POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.data.models.trading import AccountBalance
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.application.scheduler import (
    MARK_INTERVAL_SECONDS,
    CycleReport,
    PositionsScheduler,
    TrackedAccount,
)
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
    ReconciliationRunView,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_BITGET = VenueCalendar(venue="bitget", tz=ZoneInfo("UTC"), regular=KNOWN_SESSIONS["BITGET"])


def _clock() -> datetime:
    return _NOW


class FakeMarkPriceSource:
    def __init__(self, price: Money | None = None) -> None:
        self._price = price

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        return self._price


class FailingVenueMarkSource:
    """`venue == "FAIL"`인 포지션만 예외를 던지는 대역 — 계좌 하나의
    실패를 재현하려고 그 계좌의 포지션만 이 venue로 연다."""

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        if PositionKey.parse(position_key).venue == "FAIL":
            raise ConnectionError("boom")
        return Money(amount=Decimal("70000"), currency=Currency.USDT)


class BlockingMarkPriceSource:
    """동시성 테스트 전용 — `mark()` 호출 시점에 `reached`를 신호하고
    `resume`이 풀릴 때까지 대기한다. 스케줄러가 스냅샷을 읽은 직후·아직
    쓰기 전인 그 창(window)에 맞춰 동시 쓰기를 주입하려는 목적이다
    (`asyncio.Event` 기반 결정론, task-409 선례)."""

    def __init__(self, price: Money, reached: asyncio.Event, resume: asyncio.Event) -> None:
        self._price = price
        self._reached = reached
        self._resume = resume

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        self._reached.set()
        await self._resume.wait()
        return self._price


class FakeFxRateSource:
    async def rate(self, base: Currency, quote: Currency, at: datetime) -> None:  # pragma: no cover
        raise NotImplementedError("이 스위트는 통화 불일치를 쓰지 않는다")


class FailingVenueBalanceSource:
    """특정 `connection_id`만 잔고 조회에서 예외를 던지는 대역 — 계좌
    하나의 대사(reconcile) 실패를 재현한다."""

    def __init__(self, fail_connection_id) -> None:
        self._fail_connection_id = fail_connection_id

    async def balances(self, connection_id) -> list[AccountBalance]:
        if connection_id == self._fail_connection_id:
            raise ConnectionError("boom")
        return [
            AccountBalance(
                exchange="bitget", asset="BTC", total=Decimal("1"), available=Decimal("1")
            )
        ]


async def _fake_recon(
    *, tenant_id, target_type: str, target_ref, connection_id, entities: list[EntitySnapshot]
) -> ReconciliationRunView:
    """FND-08 `run_reconciliation`을 재구현하지 않는다 — 스케줄러의
    계좌별 격리만 겨냥하므로 분류 로직은 이 리프의 관심사가 아니다
    (`test_reconcile_provider.py`가 실제 FND-08로 이미 검증)."""
    return ReconciliationRunView(
        id=uuid4(),
        target_type=target_type,
        target_ref=target_ref,
        items=[],
        aggregate_classification=Classification.HEALTHY,
        created_at=_NOW,
    )


def _unique_symbol(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


async def _open_position(
    pool, *, tenant_id, account_id, quantity: Decimal, venue: str = "bitget"
) -> PositionSnapshotView:
    position_key = str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue=venue,
            instrument_id=_unique_symbol("BTCUSDT"),
            strategy_id="default",
            execution_id="paper",
        )
    )
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
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
        updated_at=_NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def _setup_account(pool) -> tuple:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    return tenant_id, account_id


async def test_run_mark_forever_updates_snapshot_and_gauge_deterministically(pool):
    """DoD #5: sleep 폴링 대신 `asyncio.Event`로 "1주기 실행 후" 시점을
    결정론적으로 잡는다(task-409 선례)."""
    tenant_id, account_id = await _setup_account(pool)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        marks=FakeMarkPriceSource(price=Money(amount=Decimal("65000"), currency=Currency.USDT)),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=tenant_id,
                account_id=account_id,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            )
        ],
        mark_interval_seconds=0.01,
        clock=_clock,
    )

    ran_once = asyncio.Event()
    original_cycle = scheduler.run_mark_cycle

    async def _tracked_cycle():
        report = await original_cycle()
        ran_once.set()
        return report

    scheduler.run_mark_cycle = _tracked_cycle  # type: ignore[method-assign]

    task = asyncio.create_task(scheduler.run_mark_forever())
    await asyncio.wait_for(ran_once.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    async with pool.acquire() as conn:
        [snapshot] = await PostgresSnapshotRepository(pool).list_open(conn, tenant_id, account_id)
    assert snapshot.mark_price == Money(amount=Decimal("65000"), currency=Currency.USDT)
    assert snapshot.mark_at == _NOW
    assert registry.gauge(POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE).samples() == {(): 1.0}


async def test_one_account_mark_failure_does_not_block_another(pool):
    """DoD #3 negative: 한 계좌의 마크 실패가 나머지 계좌를 막지 않고
    실패 카운터만 올린다."""
    failing_tenant, failing_account = await _setup_account(pool)
    await _open_position(
        pool,
        tenant_id=failing_tenant,
        account_id=failing_account,
        quantity=Decimal("1"),
        venue="FAIL",
    )
    healthy_tenant, healthy_account = await _setup_account(pool)
    await _open_position(
        pool, tenant_id=healthy_tenant, account_id=healthy_account, quantity=Decimal("1")
    )

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        marks=FailingVenueMarkSource(),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=failing_tenant,
                account_id=failing_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            ),
        ],
        clock=_clock,
    )

    report = await scheduler.run_mark_cycle()

    assert report.succeeded == [healthy_account]
    assert f"{failing_account}:mark" in report.failed
    assert registry.counter(POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL).samples() == {(): 1.0}

    async with pool.acquire() as conn:
        [healthy_snapshot] = await PostgresSnapshotRepository(pool).list_open(
            conn, healthy_tenant, healthy_account
        )
    assert healthy_snapshot.mark_price == Money(amount=Decimal("70000"), currency=Currency.USDT)


# ---- DEEPEN(task-2983, docs/audit/DEPTH_LA_LB_LC.md#727) -----------------
# negative 2건 추가(reconcile 단계 격리, 동시성 충돌 격리) / 수치 성능
# 단언 / 게이트 적색 재현.


async def test_one_account_reconcile_failure_does_not_block_another(pool):
    """negative #2: 대사(reconcile) 단계도 마크와 같은 계좌별 예외
    격리를 지킨다(모듈독스트링 "세 단계(mark/reconcile/nav) 전부") —
    지금까지는 마크 단계만 이 계약을 증명했다."""
    failing_tenant, failing_account = await _setup_account(pool)
    healthy_tenant, healthy_account = await _setup_account(pool)
    failing_connection_id = uuid4()
    healthy_connection_id = uuid4()

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        provider=FailingVenueBalanceSource(failing_connection_id),
        recon=_fake_recon,
        tracked=[
            TrackedAccount(
                tenant_id=failing_tenant,
                account_id=failing_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
                connection_id=failing_connection_id,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
                connection_id=healthy_connection_id,
            ),
        ],
        clock=_clock,
    )

    report = await scheduler.run_reconcile_cycle()

    assert report.succeeded == [healthy_account]
    assert f"{failing_account}:reconcile" in report.failed
    assert registry.counter(POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL).samples() == {(): 1.0}


async def test_concurrent_fill_during_mark_cycle_fails_closed_without_blocking_other_accounts(
    pool,
):
    """negative #3 + 적대적/동시성 증명: `mark_positions.py` 모듈독스트링이
    명시한 계약("동시 체결로 last_journal_seq가 바뀌면
    `ConcurrencyConflictError`를 그대로 전파한다")을 실제 동시 쓰기로
    재현한다. 마크 사이클이 스냅샷을 읽은 뒤(아직 쓰기 전) 다른 트랜잭션이
    먼저 같은 포지션에 체결을 반영해 `last_journal_seq`를 올리면, 마크
    사이클의 쓰기는 낡은 `expected_seq`로 충돌한다 — 스케줄러는 이 예외를
    삼키지 않고 실패로 기록하면서도(fail-closed) 다른 계좌는 계속
    처리해야 하고, 동시 체결의 새 상태를 덮어쓰면 안 된다."""
    tenant_id, account_id = await _setup_account(pool)
    snapshot = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )
    healthy_tenant, healthy_account = await _setup_account(pool)
    await _open_position(
        pool, tenant_id=healthy_tenant, account_id=healthy_account, quantity=Decimal("1")
    )

    reached = asyncio.Event()
    resume = asyncio.Event()
    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        marks=BlockingMarkPriceSource(
            Money(amount=Decimal("65000"), currency=Currency.USDT), reached, resume
        ),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=tenant_id,
                account_id=account_id,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            ),
        ],
        clock=_clock,
    )

    cycle_task = asyncio.create_task(scheduler.run_mark_cycle())
    await asyncio.wait_for(reached.wait(), timeout=5)

    # 동시 체결 흉내: 마크 사이클이 이미 읽어 든(last_journal_seq=0) 스냅샷이
    # 낡아지도록, 다른 트랜잭션이 먼저 last_journal_seq를 1로 올려 쓴다.
    bumped = snapshot.model_copy(update={"quantity": Decimal("2"), "last_journal_seq": 1})
    async with pool.acquire() as conn, conn.transaction():
        await PostgresSnapshotRepository(pool).upsert(conn, bumped, expected_seq=0)

    resume.set()
    report = await asyncio.wait_for(cycle_task, timeout=5)

    assert report.succeeded == [healthy_account]
    assert "ConcurrencyConflictError" in report.failed[f"{account_id}:mark"]

    async with pool.acquire() as conn:
        [current] = await PostgresSnapshotRepository(pool).list_open(conn, tenant_id, account_id)
    # 마크 사이클의 낡은 쓰기가 동시 체결을 덮어쓰지 않았다 -- fail-closed.
    assert current.quantity == Decimal("2")
    assert current.last_journal_seq == 1
    assert current.mark_price is None


@pytest.mark.perf
async def test_run_mark_cycle_completes_within_polling_interval_budget(pool):
    """수치 성능 단언: `run_mark_cycle()`은 폴링 주기
    (`MARK_INTERVAL_SECONDS`, §2.3 Draft 10s)마다 추적 계좌 전체를 한 번씩
    순회한다 — 계좌 수가 늘어도 그 사이클 자체가 다음 폴링 간격을 밀어낼
    만큼 오래 걸리면 안 된다. N=15개 계좌(각 1포지션, in-memory
    `FakeMarkPriceSource`로 네트워크 없이 DB 왕복만 측정)를 그 예산 안에
    처리함을 실측한다."""
    n = 15
    tracked: list[TrackedAccount] = []
    for _ in range(n):
        tenant_id, account_id = await _setup_account(pool)
        await _open_position(
            pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
        )
        tracked.append(
            TrackedAccount(
                tenant_id=tenant_id,
                account_id=account_id,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            )
        )

    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=MetricsRegistry(),
        marks=FakeMarkPriceSource(price=Money(amount=Decimal("65000"), currency=Currency.USDT)),
        fx=FakeFxRateSource(),
        tracked=tracked,
        clock=_clock,
    )

    budget_sec = MARK_INTERVAL_SECONDS
    start = time.perf_counter()
    report = await scheduler.run_mark_cycle()
    elapsed = time.perf_counter() - start

    print(f"[LB-17 run_mark_cycle] n={n} elapsed={elapsed:.3f}s (budget<{budget_sec}s)")
    assert report.succeeded == [target.account_id for target in tracked]
    assert elapsed < budget_sec, (
        f"run_mark_cycle {n}개 계좌 처리가 폴링 간격 예산({budget_sec}s)을 "
        f"넘었습니다({elapsed:.3f}s) -- 다음 주기를 밀어낼 수 있습니다."
    )


async def test_gate_red_when_account_isolation_removed_existing_negative_would_flip(
    pool, monkeypatch
):
    """게이트 적색 재현: `run_mark_cycle()`의 계좌별 try/except 격리
    (모듈독스트링 "계좌 단위 예외 격리", §9.3 LB-17 DoD)를 제거한 회귀를
    주입하면, 실패 계좌의 예외가 사이클 전체를 중단시켜 정상 계좌까지
    처리되지 않는다 — `test_one_account_mark_failure_does_not_block_another`
    가 지키는 "정상 계좌는 계속 갱신됨" 단언이 green에서 red로 뒤집힘을
    이 자리에서 직접 재현한다."""
    failing_tenant, failing_account = await _setup_account(pool)
    await _open_position(
        pool,
        tenant_id=failing_tenant,
        account_id=failing_account,
        quantity=Decimal("1"),
        venue="FAIL",
    )
    healthy_tenant, healthy_account = await _setup_account(pool)
    await _open_position(
        pool, tenant_id=healthy_tenant, account_id=healthy_account, quantity=Decimal("1")
    )

    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=MetricsRegistry(),
        marks=FailingVenueMarkSource(),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=failing_tenant,
                account_id=failing_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=_BITGET,
            ),
        ],
        clock=_clock,
    )

    async def _regressed_run_mark_cycle(self: PositionsScheduler) -> CycleReport:
        # 회귀: 계좌별 try/except 격리 없이 순차 호출한다 -- 실패 계좌의
        # 예외가 사이클 전체를 그대로 중단시킨다.
        report = CycleReport()
        for target in self._tracked:
            await mark_positions(
                target.tenant_id,
                target.account_id,
                snapshots=self._snapshots,
                marks=self._marks,
                fx=self._fx,
                pool=self._pool,
                clock=self._clock,
            )
            report.succeeded.append(target.account_id)
        return report

    monkeypatch.setattr(PositionsScheduler, "run_mark_cycle", _regressed_run_mark_cycle)

    async def _assert_existing_negative_still_holds() -> None:
        report = await scheduler.run_mark_cycle()
        assert report.succeeded == [healthy_account]

    with pytest.raises(pytest.fail.Exception):
        try:
            await _assert_existing_negative_still_holds()
        except ConnectionError as exc:
            pytest.fail(f"계좌 격리가 사라지면 실패 계좌 예외가 사이클 전체를 막습니다: {exc}")
