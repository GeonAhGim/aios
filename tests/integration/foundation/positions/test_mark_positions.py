"""LB-14 `mark_positions`/`CandleMarkPriceSource`/`CandleFxRateSource`
통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.
DoD(task-654): "스테일 → None". `pos_snapshot`은 실제 Postgres(조건부
upsert 경로까지 검증)를 쓰고, market_data 캔들/참조데이터는 이 리프의
관심사가 아니므로 in-memory fake로 대역한다(LA-13/LA-12는 각자의 리프가
이미 실DB로 검증했다).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import AssetClass, Currency, Money
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    InstrumentRef,
    SeriesKey,
    SymbolStatus,
    Timeframe,
    Venue,
)
from src.foundation.positions.adapters.candle_mark_price_source import CandleMarkPriceSource
from src.foundation.positions.adapters.fx_rate_source import CandleFxRateSource
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_M1 = Timeframe.M1


def _clock() -> datetime:
    return _NOW


class FakeCandleStore:
    """`CandleStore` 대역 — `open_time` 오름차순 리스트를 시리즈키별로 보관."""

    def __init__(self) -> None:
        self._series: dict[tuple[Venue, object, Timeframe], list[CandleRecord]] = {}

    def seed(self, key: SeriesKey, candles: list[CandleRecord]) -> None:
        self._series[(key.venue, key.instrument_id, key.timeframe)] = sorted(
            candles, key=lambda c: c.open_time
        )

    async def upsert_batch(self, conn, batch_id, candles):  # pragma: no cover - 미사용
        raise NotImplementedError

    async def quarantine(self, conn, batch_id, candles, issues):  # pragma: no cover
        raise NotImplementedError

    async def query(self, conn, key, start, end, as_of):
        rows = self._series.get((key.venue, key.instrument_id, key.timeframe), [])
        return [c for c in rows if start <= c.open_time < end]

    async def last_open_time(self, conn, key):
        rows = self._series.get((key.venue, key.instrument_id, key.timeframe), [])
        return rows[-1].open_time if rows else None


class FakeReferenceRepository:
    def __init__(self) -> None:
        self._instruments: dict[tuple[Venue, str], InstrumentRef] = {}

    def seed(self, venue: Venue, venue_symbol: str, ref: InstrumentRef) -> None:
        self._instruments[(venue, venue_symbol)] = ref

    async def get_instrument(self, conn, venue, canonical, at):
        return self._instruments.get((venue, canonical))

    async def register(self, conn, cmd):  # pragma: no cover - 미사용
        raise NotImplementedError

    async def add_alias(self, conn, instrument_id, venue, venue_symbol):  # pragma: no cover
        raise NotImplementedError

    async def list_actions(self, conn, instrument_id):
        return []

    async def record_action(self, conn, action):  # pragma: no cover - 미사용
        raise NotImplementedError


class FakeNullPool:
    """`asyncpg.Pool`처럼 `.acquire()`를 지원해야 하는 자리에, 실제로 `conn`을
    쓰지 않는 대역(`FakeSnapshotRepository`/`FakeCandleStore` 등)과 짝지어
    실DB 없이 배선을 완성하는 용도의 최소 스텁."""

    def acquire(self) -> _AsyncNullContext:
        return _AsyncNullContext()


class _AsyncNullContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeSnapshotRepository:
    """`SnapshotRepository` 대역 — Postgres 어댑터는 `avg_cost`/`mark_price`
    통화를 항상 `pos_account.base_currency`로 강제한다(`postgres_snapshot_
    repository.py` 모듈독스트링, 스키마에 별도 통화 컬럼이 없다는 알려진
    제약). `avg_cost.currency != base_currency`(FX 경로가 실제로 필요한
    경우)를 실DB 없이 재현하려고 이 리프에서만 in-memory로 대역한다."""

    def __init__(self) -> None:
        self._rows: dict[str, PositionSnapshotView] = {}

    def seed(self, snapshot: PositionSnapshotView) -> None:
        self._rows[snapshot.position_key] = snapshot

    async def get(self, conn, tenant_id, position_key):
        row = self._rows.get(position_key)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def upsert(self, conn, snapshot, expected_seq):
        current = self._rows.get(snapshot.position_key)
        if current is not None and current.last_journal_seq != expected_seq:
            raise ConcurrencyConflictError(snapshot.position_key)
        self._rows[snapshot.position_key] = snapshot
        return snapshot

    async def list_open(self, conn, tenant_id, account_id):
        return [
            row
            for row in self._rows.values()
            if row.tenant_id == tenant_id and row.account_id == account_id and row.quantity != 0
        ]


class FakeCalendarRepository:
    """BITGET(continuous)만 쓰는 테스트라 호출되면 그 자체가 결함 신호다."""

    async def load(self, conn, venue, year):  # pragma: no cover - 방어용
        raise NotImplementedError(f"BITGET은 continuous라 calendar.load가 필요 없다: {venue}")

    async def upsert_days(self, conn, venue, days):  # pragma: no cover
        raise NotImplementedError


def _instrument_id():
    return uuid4()


def _instrument_ref(instrument_id, *, venue_symbol: str, quote: str) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=instrument_id,
        venue=Venue.BITGET,
        canonical_symbol=f"{venue_symbol}-canonical",
        venue_symbol=venue_symbol,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote=quote,
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.0001"),
        status=SymbolStatus.LISTED,
        listed_at=_NOW - timedelta(days=365),
        delisted_at=None,
    )


def _candle(key: SeriesKey, open_time: datetime, close: Decimal) -> CandleRecord:
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
    )


def _unique_symbol(prefix: str) -> str:
    """`pos_snapshot.position_key`는 전역 PK다 — 영속 테스트 DB에 이전 실행이
    남긴 같은 문자열이 있으면 `ON CONFLICT DO UPDATE`가 tenant_id/account_id는
    갱신하지 않은 채(그 두 컬럼은 SET 절에 없다, `postgres_snapshot_repository.
    py`) 다른 컬럼만 덮어써 `list_open`이 새 tenant로는 그 행을 못 찾는
    조용한 실패가 난다 — 매 호출 유일한 접미사로 그 충돌을 막는다."""
    return f"{prefix}{uuid4().hex[:8]}"


def _position_key(venue_symbol: str) -> str:
    return str(
        PositionKey(
            venue="bitget",
            instrument_id=venue_symbol,
            strategy_id="default",
            execution_id="paper",
        )
    )


async def _open_position(
    pool, *, tenant_id, account_id, position_key: str, base_currency: Currency, quantity: Decimal
) -> PositionSnapshotView:
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("60000"), currency=base_currency),
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
        updated_at=_NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


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
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = _unique_symbol("BTCUSDT")
    position_key = _position_key(symbol)
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )

    instrument_id = _instrument_id()
    ref = _instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=_M1)
    store.seed(key, [_candle(key, _NOW - timedelta(seconds=30), Decimal("65000.5"))])

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id, account_id, snapshots=PostgresSnapshotRepository(pool), marks=marks, fx=fx,
        pool=pool, clock=_clock,
    )

    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.USDT)
    assert result.mark_at == _NOW
    assert result.unrealized_pnl_base == Decimal("5000.5")


async def test_stale_candle_clears_previous_mark_instead_of_keeping_it(pool, refs, store, cal):
    """task-654 decision: 스테일 마크는 직전값을 그대로 두지 않고 None으로
    덮어써야 한다 — 조용한 오평가 방지가 핵심 DoD다."""
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = _unique_symbol("BTCUSDT")
    position_key = _position_key(symbol)
    snapshot = await _open_position(
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
            "mark_at": _NOW - timedelta(hours=1),
            "unrealized_pnl_base": Decimal("1000"),
        }
    )
    async with pool.acquire() as conn, conn.transaction():
        await PostgresSnapshotRepository(pool).upsert(conn, stale_snapshot, expected_seq=0)

    instrument_id = _instrument_id()
    ref = _instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=_M1)
    # 3×1분 임계(LA-5)를 넘는 10분 전 캔들 — STALE.
    store.seed(key, [_candle(key, _NOW - timedelta(minutes=10), Decimal("65000.5"))])

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id, account_id, snapshots=PostgresSnapshotRepository(pool), marks=marks, fx=fx,
        pool=pool, clock=_clock,
    )

    assert result.mark_price is None
    assert result.mark_at is None
    assert result.unrealized_pnl_base is None


async def test_unknown_instrument_yields_none_mark(pool, refs, store, cal):
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    position_key = _position_key(_unique_symbol("UNKNOWNSYM"))
    await _open_position(
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
        tenant_id, account_id, snapshots=PostgresSnapshotRepository(pool), marks=marks, fx=fx,
        pool=pool, clock=_clock,
    )

    assert result.mark_price is None
    assert result.unrealized_pnl_base is None


async def test_missing_fx_keeps_mark_but_clears_unrealized(refs, store, cal):
    """`avg_cost.currency`(=마크 통화, USDT)가 `base_currency`(KRW)와 달라
    FX가 실제로 필요한 경우 — Postgres 어댑터는 두 통화를 항상 강제로
    같게 만들므로(모듈독스트링) 이 조합은 in-memory `FakeSnapshotRepository`
    로만 재현할 수 있다."""
    tenant_id, account_id = uuid4(), uuid4()
    symbol = _unique_symbol("BTCUSDT")
    position_key = _position_key(symbol)
    snapshots = FakeSnapshotRepository()
    snapshots.seed(
        PositionSnapshotView(
            position_key=position_key,
            tenant_id=tenant_id,
            account_id=account_id,
            instrument_id=uuid4(),
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
            updated_at=_NOW,
        )
    )

    instrument_id = _instrument_id()
    ref = _instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=_M1)
    store.seed(key, [_candle(key, _NOW - timedelta(seconds=30), Decimal("65000.5"))])

    null_pool = FakeNullPool()
    marks = CandleMarkPriceSource(null_pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(null_pool, store=store, cal=cal, references={})  # USDT/KRW 미설정

    [result] = await mark_positions(
        tenant_id, account_id, snapshots=snapshots, marks=marks, fx=fx,
        pool=null_pool, clock=_clock,
    )

    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.USDT)
    assert result.mark_at == _NOW
    assert result.unrealized_pnl_base is None


async def test_fx_median_of_two_reference_legs(pool, store, cal):
    """`CandleFxRateSource`가 §9.3 LB-14 "Bitget·KIS 참조 시세 중앙값"을
    실제로 계산하는지 — 두 참조 시리즈의 중앙값(평균, 짝수 개)."""
    leg_a = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=_M1)
    leg_b = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=_M1)
    store.seed(leg_a, [_candle(leg_a, _NOW - timedelta(seconds=10), Decimal("1350.0"))])
    store.seed(leg_b, [_candle(leg_b, _NOW - timedelta(seconds=10), Decimal("1360.0"))])

    fx = CandleFxRateSource(
        pool, store=store, cal=cal, references={(Currency.USDT, Currency.KRW): [leg_a, leg_b]}
    )

    rate = await fx.rate(Currency.USDT, Currency.KRW, _NOW)

    assert rate is not None
    assert rate.rate == Decimal("1355.0")
    assert rate.base is Currency.USDT
    assert rate.quote is Currency.KRW


async def test_unknown_venue_position_key_yields_none_mark(pool, refs, store, cal):
    """positions 도메인은 `TESTVENUE` 같은 market_data 밖 venue 문자열도
    허용한다(다른 통합테스트 픽스처 관례) — 캔들 소스가 없을 뿐 오류는
    아니다."""
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="TESTVENUE", base_currency=Currency.USDT
    )
    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=_unique_symbol("BTCUSDT"),
            strategy_id="default",
            execution_id="paper",
        )
    )
    await _open_position(
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
        tenant_id, account_id, snapshots=PostgresSnapshotRepository(pool), marks=marks, fx=fx,
        pool=pool, clock=_clock,
    )

    assert result.mark_price is None
