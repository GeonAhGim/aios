# ratchet-allow: test doubles (FakeCandleStore, FakeReferenceRepository,
#   FakeCalendarRepository) use raise NotImplementedError as fail-closed guards —
#   any call path reaching these stubs indicates a bug in the test or code under test.
"""LB-14 `mark_positions` 깊이 하한 보강 (DEEPEN task-24604).

음성(negative) 4종, 실패주입 1종, 성능 1종, 게이트 적색 재현 1종.
기존 테스트 파일을 수정하지 않고 신규 파일로 분리(CLAUDE.md 빈발 실수 #12).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency, Money
from src.foundation.market_data.contracts.v1 import SeriesKey, Venue
from src.foundation.positions.adapters.candle_mark_price_source import CandleMarkPriceSource
from src.foundation.positions.adapters.fx_rate_source import CandleFxRateSource
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from tests.integration.foundation.positions.mark_positions_doubles import (
    M1,
    NOW,
    FakeCalendarRepository,
    FakeCandleStore,
    FakeMarkPriceSource,
    FakeNullPool,
    FakeReferenceRepository,
    FakeSnapshotRepository,
    candle,
    clock,
    instrument_id,
    instrument_ref,
    position_key,
    unique_symbol,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


def _snap(
    tenant_id,
    account_id,
    pkey,
    *,
    qty=Decimal("1"),
    base=Currency.USDT,
    seq=0,
    mark=None,
    mark_at=None,
    pnl=None,
):
    """인메모리 포지션 스냅샷 생성 헬퍼."""
    return PositionSnapshotView(
        position_key=pkey,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=instrument_id(),
        quantity=qty,
        avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=pnl,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=mark,
        mark_at=mark_at,
        base_currency=base,
        last_journal_seq=seq,
        updated_at=NOW,
    )


def _seed_one(store, refs, tenant_id, account_id, snapshots, prefix, mark_price=Decimal("65000.5")):
    """포지션 1개 + 캔들/참조데이터 시드. (prefix)는 unique_symbol 접미사."""
    sym = unique_symbol(prefix)
    pkey = position_key(tenant_id, sym)
    snapshots.seed(_snap(tenant_id, account_id, pkey))
    iid = instrument_id()
    refs.seed(Venue.BITGET, sym, instrument_ref(iid, venue_symbol=sym, quote="USDT"))
    key = SeriesKey(venue=Venue.BITGET, instrument_id=iid, timeframe=M1)
    store.seed(key, [candle(key, NOW - timedelta(seconds=30), mark_price)])
    return pkey


# --- 음성(negative) 테스트 ---


async def test_empty_account_returns_empty_list():
    """negative(1/3) -- `list_open`이 `[]`면 `mark_positions`도 `[]`."""
    snapshots = FakeSnapshotRepository()
    marks = FakeMarkPriceSource(Money(amount=Decimal("65000"), currency=Currency.USDT))
    fx = CandleFxRateSource(
        FakeNullPool(), store=FakeCandleStore(), cal=FakeCalendarRepository(), references={}
    )
    results = await mark_positions(
        uuid4(), uuid4(), snapshots=snapshots, marks=marks, fx=fx, pool=FakeNullPool(), clock=clock
    )
    assert results == []


async def test_same_currency_skips_fx_call():
    """negative(2/3) -- avg_cost와 base_currency가 모두 USDT면 fx.rate를
    호출하지 않는다. 호출되면 실패하는 FX 대역으로 검증.
    `unrealized_pnl_base`가 `5000.5`인지 단언."""
    tenant_id, account_id = uuid4(), uuid4()
    snapshots = FakeSnapshotRepository()
    snapshots.seed(_snap(tenant_id, account_id, position_key(tenant_id, unique_symbol("SAMEFX"))))

    class _NoRateFx:
        async def rate(self, base, quote, at):
            raise AssertionError("fx.rate should NOT be called")

    marks = FakeMarkPriceSource(Money(amount=Decimal("65000.5"), currency=Currency.USDT))
    results = await mark_positions(
        tenant_id,
        account_id,
        snapshots=snapshots,
        marks=marks,
        fx=_NoRateFx(),
        pool=FakeNullPool(),
        clock=clock,
    )
    assert results[0].unrealized_pnl_base == Decimal("5000.5")


async def test_concurrent_conflict_propagates():
    """negative(3/3) -- `upsert`가 `ConcurrencyConflictError`를 던지면
    `mark_positions`가 그대로 전파한다."""
    tenant_id, account_id = uuid4(), uuid4()
    pkey = position_key(tenant_id, unique_symbol("CONCFL"))

    # upsert가 항상 충돌을 일으키도록 대역 오버라이드
    class _ConflictSnapshots(FakeSnapshotRepository):
        async def upsert(self, conn, snapshot, expected_seq):
            raise ConcurrencyConflictError(snapshot.position_key)

    conflicting = _ConflictSnapshots()
    conflicting.seed(_snap(tenant_id, account_id, pkey))
    marks = FakeMarkPriceSource(Money(amount=Decimal("65000"), currency=Currency.USDT))
    fx = CandleFxRateSource(
        FakeNullPool(), store=FakeCandleStore(), cal=FakeCalendarRepository(), references={}
    )
    with pytest.raises(ConcurrencyConflictError):
        await mark_positions(
            tenant_id,
            account_id,
            snapshots=conflicting,
            marks=marks,
            fx=fx,
            pool=FakeNullPool(),
            clock=clock,
        )


async def test_none_mark_clears_all_mark_fields_in_memory():
    """negative(4/4) -- 이전 마크가 있던 스냅샷에 마크가 `None`이면
    세 필드가 모두 `None`이 된다. 인메모리 저장소로 직접 확인."""
    tenant_id, account_id = uuid4(), uuid4()
    snapshots = FakeSnapshotRepository()
    pkey = position_key(tenant_id, unique_symbol("STALEM"))
    snapshots.seed(
        _snap(
            tenant_id,
            account_id,
            pkey,
            mark=Money(amount=Decimal("61000"), currency=Currency.USDT),
            mark_at=NOW - timedelta(hours=1),
            pnl=Decimal("1000"),
        )
    )
    marks = FakeMarkPriceSource(None)
    fx = CandleFxRateSource(
        FakeNullPool(), store=FakeCandleStore(), cal=FakeCalendarRepository(), references={}
    )
    results = await mark_positions(
        tenant_id,
        account_id,
        snapshots=snapshots,
        marks=marks,
        fx=fx,
        pool=FakeNullPool(),
        clock=clock,
    )
    assert results[0].mark_price is None
    assert results[0].mark_at is None
    assert results[0].unrealized_pnl_base is None


# --- 실패주입 테스트 ---


async def test_mark_source_failure_propagates_and_does_not_write():
    """실패주입(1/1) -- 마크 대역이 예외를 던지면 전파하고 저장소에
    반영되지 않는다."""
    tenant_id, account_id = uuid4(), uuid4()
    snapshots = FakeSnapshotRepository()
    pkey = position_key(tenant_id, unique_symbol("MARKFAIL"))
    snap = _snap(tenant_id, account_id, pkey)
    snapshots.seed(snap)

    class _BoomMark:
        async def mark(self, position_key, at):
            raise RuntimeError("simulated mark source failure")

    fx = CandleFxRateSource(
        FakeNullPool(), store=FakeCandleStore(), cal=FakeCalendarRepository(), references={}
    )
    with pytest.raises(RuntimeError, match="simulated mark source failure"):
        await mark_positions(
            tenant_id,
            account_id,
            snapshots=snapshots,
            marks=_BoomMark(),
            fx=fx,
            pool=FakeNullPool(),
            clock=clock,
        )
    assert snapshots._rows[pkey] is snap
    assert snapshots._rows[pkey].mark_price is None


# --- 성능 / 게이트 적색 재현 테스트 ---

_BUDGET_MS = 15.0  # 실측 wall ≈ 0.5ms x 30


def _perf_setup(prefix):
    tenant_id, account_id = uuid4(), uuid4()
    store, refs, cal = FakeCandleStore(), FakeReferenceRepository(), FakeCalendarRepository()
    snapshots = FakeSnapshotRepository()
    for i in range(5):
        _seed_one(store, refs, tenant_id, account_id, snapshots, f"{prefix}{i}")
    marks = CandleMarkPriceSource(FakeNullPool(), store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(FakeNullPool(), store=store, cal=cal, references={})

    async def _run(marks_=marks):
        return await mark_positions(
            tenant_id,
            account_id,
            snapshots=snapshots,
            marks=marks_,
            fx=fx,
            pool=FakeNullPool(),
            clock=clock,
        )

    return marks, _run


async def _best_wall_ms(run, n=5):
    """best-of-n 최소 wall_ms (PerfBudget.sample_async 반복)."""
    await run()  # warmup
    perf = PerfBudget()
    return min([(await perf.sample_async(run)).wall_ms for _ in range(n)])


@pytest.mark.perf
async def test_mark_positions_depth1_perf_budget():
    """수치 성능 단언 -- 포지션 5개 `mark_positions()` best-of-5 wall_ms.

    실측(2026-11-13, 로컬): wall ≈ 0.5ms. 예산 15ms = 실측 x 30.
    근거: LB-17 스케줄러 폴링 10s 대비 0.15%, CI CPU 경합 허용.
    """
    reset_virtual_totals()
    _, run = _perf_setup("PERFD1")
    best = await _best_wall_ms(run)
    print(f"[LB-14 depth1] n=5 best_wall={best:.3f}ms budget<{_BUDGET_MS}ms")
    assert best < _BUDGET_MS


@pytest.mark.perf
async def test_mark_positions_gate_red_reproduction():
    """게이트 적색 재현 -- 정상은 예산 통과, 마크 호출마다 가상 지연
    (예산의 2배 x 5호출 = 예산의 10배) 주입 시 같은 단언이 적색."""
    reset_virtual_totals()
    marks, run = _perf_setup("RED")
    assert await _best_wall_ms(run) < _BUDGET_MS

    slow = SimpleNamespace(mark=virtual_delayed_async(marks.mark, seconds=2 * _BUDGET_MS / 1000))

    async def slow_run():
        return await run(slow)

    injected = await _best_wall_ms(slow_run)
    reset_virtual_totals()
    assert injected >= 10 * _BUDGET_MS, injected
    with pytest.raises(AssertionError):
        assert injected < _BUDGET_MS
