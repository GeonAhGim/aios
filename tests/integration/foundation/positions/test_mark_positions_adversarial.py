# --- DEEPEN task-2977 — negative/거부(3) + failure-injection + 수치 성능 단언 +
# 게이트 적색 재현(동시성) + 적대적(오염된 position_key) 증명. DEPTH 감사
# (task-2723)가 원 task-654를 D1로 판정한 증빙 공백을 메운다. ---
"""LB-14 `mark_positions` adversarial/failure-injection/성능 통합테스트 —
실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-14.
행복 경로/기본 negative는 `test_mark_positions.py`에 있다.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.exceptions import CurrencyMismatchError
from src.data.models.base import Currency, Money
from src.foundation.market_data.contracts.v1 import SeriesKey, Venue
from src.foundation.positions.adapters.candle_mark_price_source import CandleMarkPriceSource
from src.foundation.positions.adapters.fx_rate_source import CandleFxRateSource
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import InvalidPositionKeyError
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account
from tests.integration.foundation.positions.mark_positions_doubles import (
    M1,
    MAX_POOL_ACQUIRE_CALLS,
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
    position_key = _position_key(tenant_id, unique_symbol("BTCUSDT"))
    await open_position(
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
            clock=clock,
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
            updated_at=NOW,
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
            clock=clock,
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
            clock=clock,
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
    symbol_a = unique_symbol("AAAUSDT")
    symbol_b = unique_symbol("BBBUSDT")
    key_a = _position_key(tenant_id, symbol_a)
    key_b = _position_key(tenant_id, symbol_b)
    await open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=key_a,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )
    await open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=key_b,
        base_currency=Currency.USDT,
        quantity=Decimal("1"),
    )
    for symbol in (symbol_a, symbol_b):
        instrument_id = _instrument_id()
        ref = instrument_ref(instrument_id, venue_symbol=symbol, quote="USDT")
        refs.seed(Venue.BITGET, symbol, ref)
        series_key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=M1)
        store.seed(
            series_key, [candle(series_key, NOW - timedelta(seconds=30), Decimal("65000.5"))]
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
            clock=clock,
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
    symbols = [unique_symbol(f"PERF{i}") for i in range(3)]
    for symbol in symbols:
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
        clock=clock,
    )

    assert len(results) == 3
    print(
        f"[LB-14 mark_positions] 3 positions pool.acquire() calls={acquire_calls} "
        f"(max={MAX_POOL_ACQUIRE_CALLS})"
    )
    assert acquire_calls <= MAX_POOL_ACQUIRE_CALLS, (
        f"포지션 3개 마크 갱신의 pool.acquire() 호출 수({acquire_calls})가 상한"
        f"({MAX_POOL_ACQUIRE_CALLS})을 초과했습니다 — 커넥션 획득 횟수 회귀입니다."
    )
