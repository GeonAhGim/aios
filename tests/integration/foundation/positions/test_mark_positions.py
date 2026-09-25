"""LB-14 `mark_positions`/`CandleMarkPriceSource`/`CandleFxRateSource`
통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.
DoD(task-654): "스테일 → None". `pos_snapshot`은 실제 Postgres(조건부
upsert 경로까지 검증)를 쓰고, market_data 캔들/참조데이터는 이 리프의
관심사가 아니므로 in-memory fake로 대역한다(LA-13/LA-12는 각자의 리프가
이미 실DB로 검증했다).

DEEPEN task-2977의 negative/failure-injection/성능/게이트 적색/적대적
증빙은 `test_mark_positions_adversarial.py`에 있다.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency, Money
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
