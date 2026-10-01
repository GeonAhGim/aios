# ratchet-allow: test doubles (FakeCandleStore, FakeReferenceRepository,
#   FakeCalendarRepository) use raise NotImplementedError as fail-closed guards —
#   any call path reaching these stubs indicates a bug in the test or code under test.
"""LB-14 `mark_positions`/`CandleMarkPriceSource`/`CandleFxRateSource`
통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.
DoD(task-654): "스테일 → None". `pos_snapshot`은 실제 Postgres(조건부
upsert 경로까지 검증)를 쓰고, market_data 캔들/참조데이터는 이 리프의
관심사가 아니므로 in-memory fake로 대역한다(LA-13/LA-12는 각자의 리프가
이미 실DB로 검증했다).

DEEPEN task-2977의 negative/failure-injection/성능/게이트 적색/적대적
증빙은 `test_mark_positions_adversarial.py`에 있다. DEEPEN task-10518이
이 파일 자신에도 negative(3)/성능(perf_budget) 증빙을 추가한다(아래
`test_stale_fx_rate_clears_unrealized_but_keeps_mark` 이하).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency, FXRate, Money
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.market_data.contracts.v1 import SeriesKey, Venue
from src.foundation.positions.adapters.candle_mark_price_source import CandleMarkPriceSource
from src.foundation.positions.adapters.fx_rate_source import CandleFxRateSource
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
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


async def test_fresh_mark_updates_unrealized_same_currency(pool, refs, store, cal):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = unique_symbol("BTCUSDT")
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
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=M1)
    store.seed(key, [candle(key, NOW - timedelta(seconds=30), Decimal("65000.5"))])

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=clock,
    )

    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.USDT)
    assert result.mark_at == NOW
    assert result.unrealized_pnl_base == Decimal("5000.5")


async def test_stale_candle_clears_previous_mark_instead_of_keeping_it(pool, refs, store, cal):
    """task-654 decision: 스테일 마크는 직전값을 그대로 두지 않고 None으로
    덮어써야 한다 — 조용한 오평가 방지가 핵심 DoD다."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = unique_symbol("BTCUSDT")
    position_key = _position_key(tenant_id, symbol)
    snapshot = await open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )
    stale_snapshot = snapshot.model_copy(
        update={
            "mark_price": Money(amount=Decimal("61000"), currency=Currency.USDT),
            "mark_at": NOW - timedelta(hours=1),
            "unrealized_pnl_base": Decimal("1000"),
        }
    )
    async with pool.acquire() as conn, conn.transaction():
        await PostgresSnapshotRepository(pool).upsert(
            conn, stale_snapshot, expected_seq=snapshot.last_journal_seq
        )

    instrument_id = _instrument_id()
    ref = instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=M1)
    # 3×1분 임계(LA-5)를 넘는 10분 전 캔들 — STALE.
    store.seed(key, [candle(key, NOW - timedelta(minutes=10), Decimal("65000.5"))])

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=clock,
    )

    assert result.mark_price is None
    assert result.mark_at is None
    assert result.unrealized_pnl_base is None


async def test_unknown_instrument_yields_none_mark(pool, refs, store, cal):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    position_key = _position_key(tenant_id, unique_symbol("UNKNOWNSYM"))
    await open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=clock,
    )

    assert result.mark_price is None
    assert result.unrealized_pnl_base is None


async def test_missing_fx_keeps_mark_but_clears_unrealized(refs, store, cal):
    """`avg_cost.currency`(=마크 통화, USDT)가 `base_currency`(KRW)와 달라
    FX가 실제로 필요한 경우 — Postgres 어댑터는 두 통화를 항상 강제로
    같게 만들므로(모듈독스트링) 이 조합은 in-memory `FakeSnapshotRepository`
    로만 재현할 수 있다."""
    tenant_id, account_id = uuid4(), uuid4()
    symbol = unique_symbol("BTCUSDT")
    position_key = _position_key(tenant_id, symbol)
    snapshots = FakeSnapshotRepository()
    snapshots.seed(
        PositionSnapshotView(
            position_key=position_key,
            tenant_id=tenant_id,
            account_id=account_id,
            instrument_id=_instrument_id(),
            quantity=Decimal("1"),
            avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
            cost_method=CostMethod.FIFO,
            lots=[],
            realized_pnl_base=Decimal("0"),
            unrealized_pnl_base=None,
            fees_base=Decimal("0"),
            funding_base=Decimal("0"),
            mark_price=None,
            mark_at=None,
            base_currency=Currency.KRW,
            last_journal_seq=0,
            updated_at=NOW,
        )
    )

    instrument_id = _instrument_id()
    ref = instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=M1)
    store.seed(key, [candle(key, NOW - timedelta(seconds=30), Decimal("65000.5"))])

    null_pool = FakeNullPool()
    marks = CandleMarkPriceSource(null_pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(null_pool, store=store, cal=cal, references={})  # USDT/KRW 미설정

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


async def test_fx_median_of_two_reference_legs(pool, store, cal):
    """`CandleFxRateSource`가 §9.3 LB-14 "Bitget·KIS 참조 시세 중앙값"을
    실제로 계산하는지 — 두 참조 시리즈의 중앙값(평균, 짝수 개)."""
    leg_a = SeriesKey(venue=Venue.BITGET, instrument_id=_instrument_id(), timeframe=M1)
    leg_b = SeriesKey(venue=Venue.BITGET, instrument_id=_instrument_id(), timeframe=M1)
    store.seed(leg_a, [candle(leg_a, NOW - timedelta(seconds=10), Decimal("1350.0"))])
    store.seed(leg_b, [candle(leg_b, NOW - timedelta(seconds=10), Decimal("1360.0"))])

    fx = CandleFxRateSource(
        pool, store=store, cal=cal, references={(Currency.USDT, Currency.KRW): [leg_a, leg_b]}
    )

    rate = await fx.rate(Currency.USDT, Currency.KRW, NOW)

    assert rate is not None
    assert rate.rate == Decimal("1355.0")
    assert rate.base is Currency.USDT
    assert rate.quote is Currency.KRW


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


async def test_unknown_venue_position_key_yields_none_mark(pool, refs, store, cal):
    """positions 도메인은 `TESTVENUE` 같은 market_data 밖 venue 문자열도
    허용한다(다른 통합테스트 픽스처 관례) — 캔들 소스가 없을 뿐 오류는
    아니다."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="TESTVENUE", base_currency=Currency.USDT
    )
    position_key = str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="TESTVENUE",
            instrument_id=unique_symbol("BTCUSDT"),
            strategy_id="default",
            execution_id="paper",
        )
    )
    await open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=clock,
    )

    assert result.mark_price is None


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
