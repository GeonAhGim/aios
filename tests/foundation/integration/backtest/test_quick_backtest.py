"""BT-10 quick_backtest 통합 — 실DB LA-23b 컬럼 경로 + 1개월 M1 측정(§7 ≤5s).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5 BT-10,
§7(즉시 백테스트 1개월 M1 ≤5s), §9.5 BT-10,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md #1·#3.

게이트 원칙(task-1405/esc-826·`test_perf_replay.py` 선례, task 결정문): 절대
시간은 CI 게이트가 아니다 — 실측값은 §7 목표와 함께 print로만 남기고,
차단 조건은 환경 무관 지표만 건다: (1) 캔들 읽기 DB 왕복 = 1회
(`read_candles_columnar` 단일 쿼리), (2) 전략 평가 횟수 = 봉 수, (3) 체결 모델
호출 횟수 = 주문 수(봉 수와 무관 — 봉마다 체결 모델을 부르는 구조 회귀를
잡는다), (4) 결과 정합성·결정론(같은 as_of 두 번 읽어 두 번 돌리면 체결
로그 동일). negative(I-10): 왕복 계수기가 추가 쿼리를 실제로 잡는지 증명.

시딩은 `perf_replay_support.seed_candles`와 같은 COPY 경로지만 가격을 결정론
톱니(41봉 주기) 걸음으로 만들어 SMA 교차 전략이 실제 주문을 내게 한다.
"""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application import quick_backtest_fill as qf
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
    QuickBacktestResult,
    run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.contracts.v1 import (
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.integration.foundation.market_data.perf_replay_support import (
    DAY_ROW_COUNT,
    MONTH_ROW_COUNT,
    new_instrument_id,
    series_key,
)

_MONTH_TARGET_SECONDS = 5.0  # §7 즉시 백테스트(1개월 M1) ≤5s — 운영 목표, 비차단(print)
_MAX_READ_ROUND_TRIPS = 1  # read_candles_columnar 단일 SELECT
_CANDLE_COLUMNS = (
    "venue", "instrument_id", "timeframe", "open_time", "close_time",
    "open", "high", "low", "close", "volume", "quote_volume", "batch_id",
)
_CONFIG = BacktestConfigV2(
    slippage=FixedSlippage(bps=Decimal("5")),
    commission=VenueTierCommission(
        venue="BITGET", maker_bps=Decimal("2"), taker_bps=Decimal("6"), min_fee=Decimal("0")
    ),
    latency_ms=0,
    partial_fill=PartialFillConfig(max_participation_pct=Decimal("1")),
    order_types=OrderTypesConfig(limit=True, stop=True, oco=False, trailing=False),
    magnifier_tf=None,
    costs=CostsConfig(funding=False, borrow_apr=None),
    adjustments=AdjustmentsConfig(splits=False, dividends=False),
    calendar="24x7",
)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def candle_store(pool):
    return PostgresCandleStore(pool)


def _walk(i: int) -> Decimal:
    return Decimal(100 + (i * 7) % 41 - 20)  # 결정론 톱니 — 41봉 주기로 80~120 왕복


def _rows(instrument_id: uuid.UUID, batch_id: uuid.UUID, t0: datetime, n: int) -> Iterator[tuple]:
    for i in range(n):
        o, c = _walk(i), _walk(i + 1)
        yield (
            Venue.BITGET.value, instrument_id, Timeframe.M1.value, t0 + timedelta(minutes=i),
            t0 + timedelta(minutes=i + 1), o, max(o, c) + 1, min(o, c) - 1, c,
            Decimal("1000000"), None, batch_id,
        )


async def _seed(pool, *, row_count: int) -> tuple[SeriesKey, datetime, datetime]:
    """COPY 시딩(perf_replay_support 선례) — 파티션은 현재 월부터 미래로만 있으므로
    t0는 현재 이후. 반환: (key, t0, as_of)."""
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(13)")
        instrument_id = await new_instrument_id(conn)
        async with conn.transaction():
            audit_id = await conn.fetchval(
                "INSERT INTO foundation_audit_event (sequence_no, aggregate_type, aggregate_id, "
                "action, outcome, trace_id, payload_hash, payload, event_hash) VALUES "
                "($1, 'test.backtest', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
                "gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
                uuid.uuid4().int % (2**62),
            )
            batch = IngestBatchResult(
                batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET,
                instrument_id=instrument_id, timeframe=Timeframe.M1, range_start=t0,
                range_end=t0 + timedelta(minutes=row_count), request_fingerprint=uuid.uuid4().hex,
                verdict=QualityVerdict(
                    verdict=Verdict.ACCEPT, accepted=row_count, quarantined=0, rejected=0, issues=[]
                ),
                batch_hash=uuid.uuid4().hex, audit_event_id=audit_id, stored_range=None,
            )
            await PostgresBatchRepository(pool).create(conn, batch)
            await conn.copy_records_to_table(
                "md_candle", records=_rows(instrument_id, batch.batch_id, t0, row_count),
                columns=_CANDLE_COLUMNS,
            )
            as_of = await conn.fetchval("SELECT now()")
    return series_key(instrument_id), t0, as_of


async def _read_columns_counting(
    pool, store: PostgresCandleStore, key: SeriesKey, start: datetime, end: datetime,
    as_of: datetime,
) -> tuple[CandleColumns, int]:
    """같은 커넥션에서 워밍업 1회(asyncpg 코덱 조회 흡수) 후 두 번째 읽기의 쿼리 수를 센다."""
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        await store.read_candles_columnar(conn, key, start, end, as_of)
        conn.add_query_logger(_log)
        try:
            columns = await store.read_candles_columnar(conn, key, start, end, as_of)
        finally:
            conn.remove_query_logger(_log)
    return columns, len(queries)


class _SmaCross:
    """빠른/느린 SMA 교차(누적합 O(1)/봉). 롱 1단위 진입·청산만, 대기 주문 있으면 대기."""

    def __init__(self, fast: int = 5, slow: int = 20) -> None:
        self.fast, self.slow, self.calls = fast, slow, 0
        self._fast_sum = self._slow_sum = Decimal("0")

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        self.calls += 1
        n, close = len(window), window.close(-1)
        self._fast_sum += close
        self._slow_sum += close
        if n > self.fast:
            self._fast_sum -= window.close(-self.fast - 1)
        if n > self.slow:
            self._slow_sum -= window.close(-self.slow - 1)
        if n < self.slow or position.has_pending_order:
            return None
        fast_above = self._fast_sum * self.slow > self._slow_sum * self.fast
        if fast_above and position.quantity == 0:
            return OrderIntent(side=OrderSide.BUY, quantity=Decimal("1"))
        if not fast_above and position.quantity > 0:
            return OrderIntent(side=OrderSide.SELL, quantity=Decimal("1"))
        return None


def _run_counting(
    columns: CandleColumns, monkeypatch: pytest.MonkeyPatch
) -> tuple[QuickBacktestResult, _SmaCross, int, float]:
    magnify_calls = 0
    original = qf.magnify

    def _counted(*a, **k):
        nonlocal magnify_calls
        magnify_calls += 1
        return original(*a, **k)

    monkeypatch.setattr(qf, "magnify", _counted)
    strategy = _SmaCross()
    started = time.perf_counter()
    result = run_quick_backtest(
        _CONFIG, columns, timeframe=Timeframe.M1, strategy=strategy, initial_cash=Decimal("10000")
    )
    return result, strategy, magnify_calls, time.perf_counter() - started


@pytest.mark.perf
async def test_one_month_m1_column_path_budget(pool, candle_store, monkeypatch):
    """§7 1개월(43,200) M1 — 게이트는 왕복 1회·평가 횟수·체결 모델 호출 수·정합성. 5s는 print."""
    key, t0, as_of = await _seed(pool, row_count=MONTH_ROW_COUNT)
    columns, round_trips = await _read_columns_counting(
        pool, candle_store, key, t0, t0 + timedelta(minutes=MONTH_ROW_COUNT), as_of
    )
    result, strategy, magnify_calls, elapsed = _run_counting(columns, monkeypatch)

    print(
        f"\nquick_backtest 1month/{MONTH_ROW_COUNT} M1: engine {elapsed:.3f}s "
        f"(target<{_MONTH_TARGET_SECONDS}s §7 운영 목표, 비차단); fills={len(result.fills)} "
        f"read round trips={round_trips} (max={_MAX_READ_ROUND_TRIPS}); "
        f"magnify calls={magnify_calls} (= orders, bars={result.bars})"
    )
    assert len(columns) == MONTH_ROW_COUNT == result.bars == strategy.calls
    assert round_trips <= _MAX_READ_ROUND_TRIPS
    assert len(result.fills) > 100  # 톱니 걸음이면 SMA 교차가 수백 회 — 실제로 체결이 일어났다
    assert magnify_calls == len(result.fills) < result.bars  # 체결 모델은 주문마다만, 봉마다 아님
    assert all(f.remaining_quantity == 0 for f in result.fills)
    assert result.expired_orders <= 1  # 데이터 말단에 걸린 주문 최대 1건
    assert result.final_equity == result.cash + result.position_quantity * columns.close[-1]


async def test_same_as_of_twice_gives_identical_fill_log(pool, candle_store, monkeypatch):
    """A5 결정론 — 같은 as_of·구간을 두 번 읽어 두 번 돌리면 체결 로그가 동일하다."""
    key, t0, as_of = await _seed(pool, row_count=DAY_ROW_COUNT)
    end = t0 + timedelta(minutes=DAY_ROW_COUNT)
    async with pool.acquire() as conn:
        first = await candle_store.read_candles_columnar(conn, key, t0, end, as_of)
        second = await candle_store.read_candles_columnar(conn, key, t0, end, as_of)
    a, _, _, _ = _run_counting(first, monkeypatch)
    b, _, _, _ = _run_counting(second, monkeypatch)
    assert a.fills == b.fills and repr(a.fills) == repr(b.fills)
    assert a.equity_curve == b.equity_curve and len(a.fills) > 0
    assert all(f.side == OrderSide.BUY for f in a.fills[::2])  # 롱 진입/청산 교대


class _ChattyStore(PostgresCandleStore):
    """negative 전용 — 읽기마다 왕복을 하나 더 낸다(봉별 쿼리를 끼워 넣는 회귀의 최소 재현)."""

    async def read_candles_columnar(self, conn, key, start, end, as_of):
        await conn.fetchval("SELECT 1")
        return await super().read_candles_columnar(conn, key, start, end, as_of)


async def test_round_trip_gate_detects_extra_query(pool):
    """negative(I-10): 왕복 계수기가 추가 쿼리를 실제로 잡는다."""
    key, t0, as_of = await _seed(pool, row_count=DAY_ROW_COUNT)
    _, round_trips = await _read_columns_counting(
        pool, _ChattyStore(pool), key, t0, t0 + timedelta(minutes=DAY_ROW_COUNT), as_of
    )
    assert round_trips == _MAX_READ_ROUND_TRIPS + 1 > _MAX_READ_ROUND_TRIPS
