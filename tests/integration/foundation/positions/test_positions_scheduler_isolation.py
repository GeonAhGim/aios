"""LB-17 `PositionsScheduler` 계좌별 예외 격리 통합테스트 — 실 DB 대상.

DEEPEN(task-2983, docs/audit/DEPTH_LA_LB_LC.md#727): 원 리프가 negative
1건(마크 격리)만 증명했다 — 이 파일이 (1) reconcile 단계 격리 negative,
(2) 마크 사이클과 동시 체결이 경합하는 실제 낙관적 동시성 충돌(적대적/
동시성 증명 + negative #3), (3) 계좌별 격리를 제거한 회귀가 기존
negative를 green→red로 뒤집는 게이트 적색 재현을 채운다. 기본 마크
사이클 계약은 `test_positions_scheduler.py`, 수치 성능 예산은
`test_positions_scheduler_perf.py` — 대역/헬퍼는 `scheduler_test_doubles.py`
공용(RATCHET-split, task-10203).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.observability.metric_names import POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.application.scheduler import (
    CycleReport,
    PositionsScheduler,
    TrackedAccount,
)
from tests.integration.foundation.positions.scheduler_test_doubles import (
    BITGET,
    BlockingMarkPriceSource,
    FailingVenueBalanceSource,
    FailingVenueMarkSource,
    FakeFxRateSource,
    clock,
    fake_recon,
    open_position,
    setup_account,
)


async def test_one_account_reconcile_failure_does_not_block_another(pool):
    """negative #2: 대사(reconcile) 단계도 마크와 같은 계좌별 예외
    격리를 지킨다(모듈독스트링 "세 단계(mark/reconcile/nav) 전부") —
    지금까지는 마크 단계만 이 계약을 증명했다."""
    failing_tenant, failing_account = await setup_account(pool)
    healthy_tenant, healthy_account = await setup_account(pool)
    failing_connection_id = uuid4()
    healthy_connection_id = uuid4()

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        provider=FailingVenueBalanceSource(failing_connection_id),
        recon=fake_recon,
        tracked=[
            TrackedAccount(
                tenant_id=failing_tenant,
                account_id=failing_account,
                base_currency=Currency.USDT,
                calendar=BITGET,
                connection_id=failing_connection_id,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=BITGET,
                connection_id=healthy_connection_id,
            ),
        ],
        clock=clock,
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
    tenant_id, account_id = await setup_account(pool)
    snapshot = await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )
    healthy_tenant, healthy_account = await setup_account(pool)
    await open_position(
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
                calendar=BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=BITGET,
            ),
        ],
        clock=clock,
    )

    cycle_task = asyncio.create_task(scheduler.run_mark_cycle())
    await asyncio.wait_for(reached.wait(), timeout=5)

    # 동시 체결 흉내: 마크 사이클이 이미 읽어 든(last_journal_seq=1) 스냅샷이
    # 낡아지도록, 다른 트랜잭션이 먼저 last_journal_seq를 2로 올려 쓴다.
    bumped = snapshot.model_copy(update={"quantity": Decimal("2"), "last_journal_seq": 2})
    async with pool.acquire() as conn, conn.transaction():
        await PostgresSnapshotRepository(pool).upsert(
            conn, bumped, expected_seq=snapshot.last_journal_seq
        )

    resume.set()
    report = await asyncio.wait_for(cycle_task, timeout=5)

    assert report.succeeded == [healthy_account]
    assert "ConcurrencyConflictError" in report.failed[f"{account_id}:mark"]

    async with pool.acquire() as conn:
        [current] = await PostgresSnapshotRepository(pool).list_open(conn, tenant_id, account_id)
    # 마크 사이클의 낡은 쓰기가 동시 체결을 덮어쓰지 않았다 -- fail-closed.
    assert current.quantity == Decimal("2")
    assert current.last_journal_seq == 2
    assert current.mark_price is None


async def test_gate_red_when_account_isolation_removed_existing_negative_would_flip(
    pool, monkeypatch
):
    """게이트 적색 재현: `run_mark_cycle()`의 계좌별 try/except 격리
    (모듈독스트링 "계좌 단위 예외 격리", §9.3 LB-17 DoD)를 제거한 회귀를
    주입하면, 실패 계좌의 예외가 사이클 전체를 중단시켜 정상 계좌까지
    처리되지 않는다 — `test_one_account_mark_failure_does_not_block_another`
    가 지키는 "정상 계좌는 계속 갱신됨" 단언이 green에서 red로 뒤집힘을
    이 자리에서 직접 재현한다."""
    failing_tenant, failing_account = await setup_account(pool)
    await open_position(
        pool,
        tenant_id=failing_tenant,
        account_id=failing_account,
        quantity=Decimal("1"),
        venue="FAIL",
    )
    healthy_tenant, healthy_account = await setup_account(pool)
    await open_position(
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
                calendar=BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=BITGET,
            ),
        ],
        clock=clock,
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
