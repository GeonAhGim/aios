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
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.exceptions import CurrencyMismatchError
from src.data.models.base import AssetClass, Currency, Money
from src.foundation.entities.domain.defaults import default_portfolio_id
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
from src.foundation.positions.domain.position_key import InvalidPositionKeyError, PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_M1 = Timeframe.M1
_MAX_POOL_ACQUIRE_CALLS = 12
_MAX_POOL_ACQUIRE_CALLS = 12


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


class FakeMarkPriceSource:
    """`MarkPriceSource` 대역 — 고정된(혹은 `None`) 마크를 그대로 돌려준다.
    `CandleMarkPriceSource`로는 재현할 수 없는 배선 버그(마크가 포지션의
    원가 통화가 아닌 다른 통화로 도착하는 경우)를 결정적으로 재현하는
    용도(DEEPEN task-2977, negative 1/3)."""

    def __init__(self, mark: Money | None) -> None:
        self._mark = mark

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        return self._mark


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


def _position_key(tenant_id: UUID, venue_symbol: str) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
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
        # task-3863: a real "open" position (quantity != 0) always got there via
        # record_fill folding at least one journal entry, so last_journal_seq=1
        # here (not the 0 sentinel that means "no row for this key yet") -- an
        # already-open position's mark-price replace must stay a normal CAS, not
        # collide with the adapter's first-creation-only guard at expected_seq=0.
        last_journal_seq=1,
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
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = _unique_symbol("BTCUSDT")
    position_key = _position_key(tenant_id, symbol)
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
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=_clock,
    )

    assert result.mark_price == Money(amount=Decimal("65000.5"), currency=Currency.USDT)
    assert result.mark_at == _NOW
    assert result.unrealized_pnl_base == Decimal("5000.5")


async def test_stale_candle_clears_previous_mark_instead_of_keeping_it(pool, refs, store, cal):
    """task-654 decision: 스테일 마크는 직전값을 그대로 두지 않고 None으로
    덮어써야 한다 — 조용한 오평가 방지가 핵심 DoD다."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = _unique_symbol("BTCUSDT")
    position_key = _position_key(tenant_id, symbol)
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
        await PostgresSnapshotRepository(pool).upsert(
            conn, stale_snapshot, expected_seq=snapshot.last_journal_seq
        )

    instrument_id = _instrument_id()
    ref = _instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
    refs.seed(Venue.BITGET, symbol, ref)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=_M1)
    # 3×1분 임계(LA-5)를 넘는 10분 전 캔들 — STALE.
    store.seed(key, [_candle(key, _NOW - timedelta(minutes=10), Decimal("65000.5"))])

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    [result] = await mark_positions(
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=_clock,
    )

    assert result.mark_price is None
    assert result.mark_at is None
    assert result.unrealized_pnl_base is None


async def test_unknown_instrument_yields_none_mark(pool, refs, store, cal):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    position_key = _position_key(tenant_id, _unique_symbol("UNKNOWNSYM"))
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
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=_clock,
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
    position_key = _position_key(tenant_id, symbol)
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
        tenant_id,
        account_id,
        snapshots=snapshots,
        marks=marks,
        fx=fx,
        pool=null_pool,
        clock=_clock,
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
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="TESTVENUE", base_currency=Currency.USDT
    )
    position_key = str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
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
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=_clock,
    )

    assert result.mark_price is None


# --- DEEPEN task-2977 — negative/거부(3) + failure-injection + 수치 성능 단언 +
# 게이트 적색 재현(동시성) + 적대적(오염된 position_key) 증명. DEPTH 감사
# (task-2723)가 원 task-654를 D1로 판정한 증빙 공백을 메운다. ---


async def test_currency_mismatch_between_mark_and_avg_cost_propagates(pool, store, cal):
    """negative(1/3) — 모듈독스트링: `MarkPriceSource`는 항상 포지션의 원가
    통화로 마크를 돌려줘야 한다는 배선 전제가 깨지면(예: 소스가 실수로
    다른 통화를 돌려주는 어댑터 버그) `pnl.unrealized`가 `CurrencyMismatchError`
    로 즉시 실패해야 한다 — FX로 메울 수 있는 문제가 아니므로
    `mark_positions`는 `FxRateMissingError`처럼 삼키지 않고 그대로 전파한다
    (조용한 오평가보다 크래시가 낫다는 fail-closed 선택). 스냅샷은 갱신
    시도 전에 예외가 나므로 DB에 전혀 반영되지 않는다."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    position_key = _position_key(tenant_id, _unique_symbol("BTCUSDT"))
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )

    # avg_cost는 USDT인데 마크 소스가 KRW를 돌려준다 — 배선 버그를 흉내.
    marks = FakeMarkPriceSource(Money(amount=Decimal("65000"), currency=Currency.KRW))
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    with pytest.raises(CurrencyMismatchError):
        await mark_positions(
            tenant_id,
            account_id,
            snapshots=PostgresSnapshotRepository(pool),
            marks=marks,
            fx=fx,
            pool=pool,
            clock=_clock,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mark_price, mark_at, unrealized_pnl_base FROM pos_snapshot "
            "WHERE position_key = $1",
            position_key,
        )
    assert row["mark_price"] is None
    assert row["mark_at"] is None
    assert row["unrealized_pnl_base"] is None


async def test_corrupted_position_key_raises_instead_of_marking(refs, store, cal):
    """negative(2/3) + 적대적 — `pos_snapshot` 행의 `position_key`가 FA-0d
    5필드 형식(`venue:instrument_id:strategy_id:execution_id:portfolio_id`)
    을 갖추지 못한 채 상류에서 손상/조작돼 들어오면(예: 레거시 4필드 잔재나
    적대적 조작) `PositionKey.parse`가 `InvalidPositionKeyError`로 즉시
    거부해야 한다 — 형식이 깨진 채로 마크가 조용히 `None` 처리되며
    넘어가면 그 포지션이 관리 대상에서 조용히 누락된 것과 구분이 안 된다.
    `CandleMarkPriceSource.mark`가 파싱 첫 단계에서 던지므로 Postgres
    스냅샷 대신 in-memory 대역을 써서 결정적으로 재현한다."""
    tenant_id, account_id = uuid4(), uuid4()
    snapshots = FakeSnapshotRepository()
    snapshots.seed(
        PositionSnapshotView(
            position_key="not-a-valid-5-part-position-key",
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
            base_currency=Currency.USDT,
            last_journal_seq=0,
            updated_at=_NOW,
        )
    )

    null_pool = FakeNullPool()
    marks = CandleMarkPriceSource(null_pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(null_pool, store=store, cal=cal, references={})

    with pytest.raises(InvalidPositionKeyError):
        await mark_positions(
            tenant_id,
            account_id,
            snapshots=snapshots,
            marks=marks,
            fx=fx,
            pool=null_pool,
            clock=_clock,
        )


async def test_concurrent_journal_write_causes_concurrency_conflict_gate_red(
    pool, refs, store, cal, monkeypatch
):
    """negative(3/3) + 게이트 적색 재현 + 동시성 증명 — 모듈독스트링: "동시
    체결로 `last_journal_seq`가 그 사이 바뀌면 `SnapshotRepository.upsert`가
    `ConcurrencyConflictError`를 던진다 ... 삼키지 않고 그대로 전파한다".
    이를 실제로 재현하려면 `mark_positions`가 `list_open`으로 읽은 *직후*,
    `upsert`가 실행되기 *전에* 다른 트랜잭션이 같은 행의 `last_journal_seq`
    를 먼저 올려야 한다 — `PostgresSnapshotRepository.list_open`을 감싸
    반환 직전에 별도 커넥션에서 실제 `upsert`(FA-10 no-UPDATE 트리거 때문에
    `pos_snapshot`은 raw UPDATE가 거부되므로, record_fill이 쓰는 것과 같은
    delete-and-replace 경로)로 그 경합을 주입한다(105번 표준 조건부
    UPDATE가 실제로 0행을 반환해 적색이 되는지까지 확인, 시뮬레이션이
    아니라 실제 SQL 경합)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol = _unique_symbol("BTCUSDT")
    position_key = _position_key(tenant_id, symbol)
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

    real_list_open = PostgresSnapshotRepository.list_open

    async def _list_open_then_race(self, conn, tenant_id_, account_id_):
        rows = await real_list_open(self, conn, tenant_id_, account_id_)
        # 동시 체결을 흉내: 별도 커넥션에서 record_fill과 같은 delete-and-
        # replace 경로로 이 mark_positions 호출이 알기 전에 last_journal_seq
        # 를 먼저 올린다.
        async with self._pool.acquire() as race_conn, race_conn.transaction():
            current = await self.get(race_conn, tenant_id_, position_key)
            bumped = current.model_copy(update={"last_journal_seq": current.last_journal_seq + 1})
            await self.upsert(race_conn, bumped, expected_seq=current.last_journal_seq)
        return rows

    monkeypatch.setattr(PostgresSnapshotRepository, "list_open", _list_open_then_race)

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    with pytest.raises(ConcurrencyConflictError):
        await mark_positions(
            tenant_id,
            account_id,
            snapshots=PostgresSnapshotRepository(pool),
            marks=marks,
            fx=fx,
            pool=pool,
            clock=_clock,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mark_price, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert row["mark_price"] is None, "경합에서 진 마크 갱신이 조용히 반영됐습니다"
    assert row["last_journal_seq"] == 2


async def test_snapshot_adapter_failure_mid_batch_propagates_without_silent_skip(
    pool, refs, store, cal, monkeypatch
):
    """failure-injection — 두 포지션을 순회하는 도중 두 번째 `upsert`에서
    어댑터가 커넥션 결함을 겪으면(실 DB 장애 시뮬레이션,
    `test_cross_tenant.py`의 결함주입 패턴과 동일) `mark_positions`는 그
    포지션만 조용히 건너뛰지 않고 예외를 그대로 전파한다 — 마크 누락을
    성공으로 위장하지 않는다. 먼저 처리된 한 포지션은 이미 독립적으로
    커밋돼 있다(포지션별 `upsert`가 각자 별도 트랜잭션이라는 계약,
    모듈독스트링에 배치 전체 원자성 주장은 없다)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbol_a = _unique_symbol("AAAUSDT")
    symbol_b = _unique_symbol("BBBUSDT")
    key_a = _position_key(tenant_id, symbol_a)
    key_b = _position_key(tenant_id, symbol_b)
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=key_a,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=key_b,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )
    for symbol in (symbol_a, symbol_b):
        instrument_id = _instrument_id()
        ref = _instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
        refs.seed(Venue.BITGET, symbol, ref)
        series_key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=_M1)
        store.seed(
            series_key, [_candle(series_key, _NOW - timedelta(seconds=30), Decimal("65000.5"))]
        )

    real_upsert = PostgresSnapshotRepository.upsert
    calls = 0

    async def _boom_on_second_call(self, conn, snapshot, expected_seq):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncpg.PostgresConnectionError("injected snapshot adapter connection failure")
        return await real_upsert(self, conn, snapshot, expected_seq)

    monkeypatch.setattr(PostgresSnapshotRepository, "upsert", _boom_on_second_call)

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    with pytest.raises(asyncpg.PostgresConnectionError):
        await mark_positions(
            tenant_id,
            account_id,
            snapshots=PostgresSnapshotRepository(pool),
            marks=marks,
            fx=fx,
            pool=pool,
            clock=_clock,
        )

    assert calls == 2, "두 번째 포지션에서 결함이 주입되기 전에 순회가 끝났습니다"
    async with pool.acquire() as conn:
        row_a = await conn.fetchrow(
            "SELECT mark_price FROM pos_snapshot WHERE position_key = $1", key_a
        )
        row_b = await conn.fetchrow(
            "SELECT mark_price FROM pos_snapshot WHERE position_key = $1", key_b
        )
    # `list_open`은 순서를 보장하지 않으므로, 어느 쪽이 먼저 처리됐든 "하나는
    # 성공 반영, 하나는 결함으로 미반영"이라는 불변만 순서 무관하게 확인한다.
    marked = [r for r in (row_a, row_b) if r["mark_price"] is not None]
    unmarked = [r for r in (row_a, row_b) if r["mark_price"] is None]
    assert len(marked) == 1
    assert len(unmarked) == 1


async def test_mark_positions_pool_acquire_count_regression_guard(
    pool, refs, store, cal, monkeypatch
):
    """수치 성능 단언(DoD) — task-2962/2970/2974의 동일한 결정을 따른다:
    공유 CI 환경의 절대 지연시간은 이 파일이 통제할 수 없는 변동성을
    낳으므로 게이트로 쓰지 않고, 구조 회귀(포지션당 `pool.acquire()` 호출
    수가 다시 늘어나는 실제 코드 결함 — 예: N+1 패턴 재도입)만 차단
    게이트로 잡는다. 현재 배선은 `list_open` 1회 + 포지션당(마크 조회 1회
    + upsert 1회) — FX 조회는 동일 통화라 발생하지 않는다."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    symbols = [_unique_symbol(f"PERF{i}") for i in range(3)]
    for symbol in symbols:
        position_key = _position_key(tenant_id, symbol)
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
        series_key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=_M1)
        store.seed(
            series_key, [_candle(series_key, _NOW - timedelta(seconds=30), Decimal("65000.5"))]
        )

    acquire_calls = 0
    real_acquire = type(pool).acquire

    def _counting_acquire(self, *args, **kwargs):
        nonlocal acquire_calls
        acquire_calls += 1
        return real_acquire(self, *args, **kwargs)

    # `asyncpg.pool.Pool.acquire`는 인스턴스 속성 대입이 막혀 있으므로(read-only,
    # 아마 C 확장 슬롯) 클래스 메서드를 감싼다 — 이 테스트 프로세스 안의 다른
    # Pool 인스턴스에도 같은 기간 동안 적용되지만 monkeypatch가 종료 시 원복한다.
    monkeypatch.setattr(type(pool), "acquire", _counting_acquire)

    marks = CandleMarkPriceSource(pool, store=store, refs=refs, cal=cal)
    fx = CandleFxRateSource(pool, store=store, cal=cal, references={})

    results = await mark_positions(
        tenant_id,
        account_id,
        snapshots=PostgresSnapshotRepository(pool),
        marks=marks,
        fx=fx,
        pool=pool,
        clock=_clock,
    )

    assert len(results) == 3
    print(
        f"[LB-14 mark_positions] 3 positions pool.acquire() calls={acquire_calls} "
        f"(max={_MAX_POOL_ACQUIRE_CALLS})"
    )
    assert acquire_calls <= _MAX_POOL_ACQUIRE_CALLS, (
        f"포지션 3개 마크 갱신의 pool.acquire() 호출 수({acquire_calls})가 상한"
        f"({_MAX_POOL_ACQUIRE_CALLS})을 초과했습니다 — 커넥션 획득 횟수 회귀입니다."
    )
