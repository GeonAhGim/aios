# ratchet-allow: test doubles (FakeNullPool, FakeMarkPriceSource) use raise
#   NotImplementedError as fail-closed guards — any call path reaching these
#   stubs indicates a bug in the test or code under test.
"""LB-14 `mark_positions`의 FX 경계값 negative/성능 증빙 — DEEPEN task-10518.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.

기본 계약·스테일/누락 FX 1건은 `test_mark_positions.py`, 통화 불일치/손상된
position_key/동시성 충돌/부분 실패/pool-acquire 회귀 증빙은
`test_mark_positions_adversarial.py`에 이미 있다. 이 파일은 `fx.convert`의
"환율은 있지만 스테일" 분기, "통화쌍 불일치(삼각 환산 금지)" 분기,
`pnl.unrealized`의 `quantity==0` 경계값, 그리고 수치 성능 예산(perf_budget)
을 `test_mark_positions.py`에서 분리했다(CLAUDE.md 빈발 실수 #12 — 500줄
래칫을 넘기지 않도록 책임별로 쪼갠 `_<aspect>.py` 자매 파일).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency, FXRate, Money
from src.foundation.market_data.contracts.v1 import SeriesKey, Venue
from src.foundation.positions.adapters.candle_mark_price_source import CandleMarkPriceSource
from src.foundation.positions.adapters.fx_rate_source import CandleFxRateSource
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account
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
    instrument_ref,
    open_position,
    unique_symbol,
)
from tests.integration.foundation.positions.mark_positions_doubles import (
    instrument_id as _instrument_id,
)
from tests.integration.foundation.positions.mark_positions_doubles import (
    position_key as _position_key,
)


@pytest.fixture
def refs() -> FakeReferenceRepository:
    return FakeReferenceRepository()


@pytest.fixture
def store() -> FakeCandleStore:
    return FakeCandleStore()


@pytest.fixture
def cal() -> FakeCalendarRepository:
    return FakeCalendarRepository()


def _usdt_snapshot(
    position_key: str, tenant_id, account_id, *, quantity: Decimal, base_currency: Currency
) -> PositionSnapshotView:
    return PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=_instrument_id(),
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
        base_currency=base_currency,
        last_journal_seq=0,
        updated_at=NOW,
    )


async def test_stale_fx_rate_clears_unrealized_but_keeps_mark(refs, store, cal):
    """negative(1/3) -- `fx.convert`는 `rate.timestamp`가 `max_age`
    (`DEFAULT_MAX_RATE_AGE`=5분)보다 오래되면 `FxRateStaleError`를 던진다
    (`domain/fx.py`). `rate`가 아예 없는 경우(이미 `test_missing_fx_keeps_
    mark_but_clears_unrealized`가 다룸)와 달리, 여기서는 환율이 "있긴
    하지만" 스테일하다 -- `mark_positions`는 이 경우도(`FxRateStaleError`는
    `FxRateMissingError`의 서브클래스) 조용히 삼켜 `unrealized_pnl_base`만
    `None`으로 비우고 마크 자체는 그대로 기록해야 한다(스테일 환율로
    계속 계산하는 것은 금지, 하지만 마크 자체는 fresh하므로 버리지
    않는다)."""
    tenant_id, account_id = uuid4(), uuid4()
    symbol = unique_symbol("BTCUSDT")
    position_key = _position_key(tenant_id, symbol)
    snapshots = FakeSnapshotRepository()
    snapshots.seed(
        _usdt_snapshot(
            position_key, tenant_id, account_id, quantity=Decimal("1"), base_currency=Currency.KRW
        )
    )

    instrument_id = _instrument_id()
    ref = instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=M1)
    store.seed(key, [candle(key, NOW - timedelta(seconds=30), Decimal("65000.5"))])
    leg = SeriesKey(venue=Venue.BITGET, instrument_id=_instrument_id(), timeframe=M1)
    # 10분 전 참조 레그(LA-5 3x1분 임계 초과) -- rate 자체가 스테일.
    store.seed(leg, [candle(leg, NOW - timedelta(minutes=10), Decimal("1350.0"))])

    null_pool = FakeNullPool()
    marks = CandleMarkPriceSource(null_pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(
        null_pool, store=store, cal=cal, references={(Currency.USDT, Currency.KRW): [leg]}
    )

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=snapshots,
        marks=marks,
        fx=fx,
        pool=null_pool,
        clock=clock,
    )

    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.USDT)
    assert result.mark_at == NOW
    assert result.unrealized_pnl_base is None


async def test_mismatched_fx_rate_pair_is_treated_as_missing():
    """negative(2/3) -- `fx.convert`는 `(rate.base, rate.quote)`가 요청한
    `(m.currency, to)`도 그 역방향도 아니면(삼각 환산 금지, `domain/fx.py`
    도크스트링) "환율 없음"과 동일하게 취급해 `FxRateMissingError`를
    던진다. `mark_positions`가 이 경로도 삼켜 `unrealized_pnl_base`만
    비우는지 -- 호출자가 엉뚱한 통화쌍의 `FXRate`를 넘기는 배선 버그를
    흉내낸다(전용 대역으로 결정적으로 재현, `CandleFxRateSource`는 자신이
    조회한 레그로만 `FXRate`를 만들어 이 조합을 재현할 수 없다). AIOS의
    `Currency`는 현재 USDT/KRW 둘뿐이라, 요청 쌍(USDT, KRW)과도 그 역방향
    (KRW, USDT)과도 다른 쌍은 동일 통화끼리의 퇴화된 `FXRate`(KRW/KRW)로만
    구성할 수 있다 -- 통화 혼동 배선 버그의 최소 재현."""
    tenant_id, account_id = uuid4(), uuid4()
    position_key = _position_key(tenant_id, unique_symbol("BTCUSDT"))
    snapshots = FakeSnapshotRepository()
    snapshots.seed(
        _usdt_snapshot(
            position_key, tenant_id, account_id, quantity=Decimal("1"), base_currency=Currency.KRW
        )
    )

    class _WrongPairFxRateSource:
        """`(base, quote)`가 호출자가 요청한 쌍과도 그 역방향과도 다른
        `FXRate`를 돌려주는 배선 버그 대역 -- `fx.convert`의 "삼각 환산
        금지" 분기를 강제로 탄다."""

        async def rate(self, base: Currency, quote: Currency, at) -> FXRate:
            return FXRate(
                base=Currency.KRW,
                quote=Currency.KRW,
                rate=Decimal("1"),
                timestamp=NOW,
                source="wrong-pair-double",
            )

    null_pool = FakeNullPool()
    marks = FakeMarkPriceSource(Money(amount=Decimal("65000.5"), currency=Currency.USDT))
    fx = _WrongPairFxRateSource()

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=snapshots,
        marks=marks,
        fx=fx,
        pool=null_pool,
        clock=clock,
    )

    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.USDT)
    assert result.unrealized_pnl_base is None


async def test_zero_quantity_position_skips_currency_mismatch_validation():
    """negative(3/3)/경계값 -- `pnl.unrealized`는 `snapshot.quantity == 0`이면
    `mark.currency != snapshot.avg_cost.currency`를 검사하는 분기 자체를
    건너뛰고 `unrealized=0`을 돌려준다(`domain/pnl.py`). FX 조회 자체는
    `mark_positions`가 `avg_cost.currency != base_currency`일 때 수량과
    무관하게 미리 수행하지만(§코드 경로, `fx.rate` 호출이 `quantity==0`
    가드보다 앞에 있다), 그 FX 조회가 `None`을 돌려줘도(환율 미설정)
    `quantity==0`이면 `pnl.unrealized`가 `rate`를 아예 쓰지 않으므로
    `CurrencyMismatchError`도 `FxRateMissingError`도 터지지 않고 `0`으로
    고정돼야 한다 -- 이미 청산된 포지션까지 마크/FX 배선 결함에 취약해지면
    안 된다는 방어 불변식."""
    tenant_id, account_id = uuid4(), uuid4()
    position_key = _position_key(tenant_id, unique_symbol("BTCUSDT"))
    zero_qty_snapshot = _usdt_snapshot(
        position_key, tenant_id, account_id, quantity=Decimal("0"), base_currency=Currency.KRW
    )

    class _SingleZeroQtySnapshotRepository:
        async def get(self, conn, tenant_id_, position_key_):  # pragma: no cover - 미사용
            raise NotImplementedError

        async def upsert(self, conn, snapshot, expected_seq):
            return snapshot

        async def list_open(self, conn, tenant_id_, account_id_):
            return [zero_qty_snapshot]

    class _NoRateFxRateSource:
        async def rate(self, base: Currency, quote: Currency, at):
            return None

    null_pool = FakeNullPool()
    # avg_cost(USDT)와 다른 통화(KRW)의 마크 -- quantity!=0이었다면
    # CurrencyMismatchError를 던져야 할 배선 버그를 흉내.
    marks = FakeMarkPriceSource(Money(amount=Decimal("65000.5"), currency=Currency.KRW))
    fx = _NoRateFxRateSource()

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=_SingleZeroQtySnapshotRepository(),
        marks=marks,
        fx=fx,
        pool=null_pool,
        clock=clock,
    )

    assert result.unrealized_pnl_base == Decimal("0")
    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.KRW)


@pytest.mark.perf
async def test_mark_positions_small_batch_completes_within_budget(
    pool, refs, store, cal, perf_budget
):
    """수치 성능 단언(perf_budget 픽스처, raw 타이머 금지) -- 계좌 하나에
    열린 포지션 5개를 `mark_positions()` 한 번으로 처리하는 왕복 지연이
    LB-17 스케줄러 폴링 간격(`MARK_INTERVAL_SECONDS`, §2.3 Draft 10s)의
    절반을 넘지 않아야 한다 -- in-memory `FakeCandleStore`/
    `FakeReferenceRepository`로 네트워크 구간을 제거해 Postgres 왕복
    (list_open 1회 + 포지션당 upsert 1회)만 실측한다."""
    from src.foundation.positions.application.scheduler import MARK_INTERVAL_SECONDS

    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    n = 5
    for i in range(n):
        symbol = unique_symbol(f"PERFMARK{i}")
        position_key = _position_key(tenant_id, symbol)
        await open_position(
            pool,
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            base_currency=Currency.USDT,
            quantity=Decimal("1"),
        )
        instrument_id = _instrument_id()
        ref = instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
        refs.seed(Venue.BITGET, symbol, ref)
        series_key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=M1)
        store.seed(
            series_key, [candle(series_key, NOW - timedelta(seconds=30), Decimal("65000.5"))]
        )

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    budget_sec = MARK_INTERVAL_SECONDS / 2

    async def _run():
        return await mark_positions(
            tenant_id,
            account_id,
            snapshots=PostgresSnapshotRepository(pool),
            marks=marks,
            fx=fx,
            pool=pool,
            clock=clock,
        )

    sample = await perf_budget.sample_async(_run)
    results = sample.result
    elapsed = sample.wall_ms / 1000

    print(f"[LB-14 mark_positions] n={n} elapsed={elapsed:.3f}s (budget<{budget_sec}s)")
    assert len(results) == n
    assert elapsed < budget_sec, (
        f"포지션 {n}개 mark_positions() 처리가 예산({budget_sec}s)을 "
        f"넘었습니다({elapsed:.3f}s) -- 폴링 간격을 밀어낼 수 있습니다."
    )
