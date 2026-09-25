# ratchet-allow: test doubles (FakeCandleStore, FakeReferenceRepository,
#   FakeCalendarRepository) use raise NotImplementedError as fail-closed guards —
#   any call path reaching these stubs indicates a bug in the test or code under test.
"""Shared fakes/helpers for the LB-14 `mark_positions` integration tests
(`test_mark_positions.py`, `test_mark_positions_adversarial.py`).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
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
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
M1 = Timeframe.M1
MAX_POOL_ACQUIRE_CALLS = 12


def clock() -> datetime:
    return NOW


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


def instrument_id():
    return uuid4()


def instrument_ref(instrument_id, *, venue_symbol: str, quote: str) -> InstrumentRef:
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
        listed_at=NOW - timedelta(days=365),
        delisted_at=None,
    )


def candle(key: SeriesKey, open_time: datetime, close: Decimal) -> CandleRecord:
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


def unique_symbol(prefix: str) -> str:
    """`pos_snapshot.position_key`는 전역 PK다 — 영속 테스트 DB에 이전 실행이
    남긴 같은 문자열이 있으면 `ON CONFLICT DO UPDATE`가 tenant_id/account_id는
    갱신하지 않은 채(그 두 컬럼은 SET 절에 없다, `postgres_snapshot_repository.
    py`) 다른 컬럼만 덮어써 `list_open`이 새 tenant로는 그 행을 못 찾는
    조용한 실패가 난다 — 매 호출 유일한 접미사로 그 충돌을 막는다."""
    return f"{prefix}{uuid4().hex[:8]}"


def position_key(tenant_id: UUID, venue_symbol: str) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="bitget",
            instrument_id=venue_symbol,
            strategy_id="default",
            execution_id="paper",
        )
    )


async def open_position(
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
        updated_at=NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)

