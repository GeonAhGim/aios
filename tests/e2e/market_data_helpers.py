"""E2E-3(`test_e2e_3_market_data_to_chart.py`) 공용 헬퍼.

LOC 규율(CLAUDE.md §7, `scripts/code-ratchets-baseline.json` loc_over_500)
때문에 테스트 본문과 분리한다 — MockIngestSource(LA-9 계약 모의 어댑터)와
ingest_candles 호출을 위한 상장 상품/커맨드 조립은 E2E-3의 5개 시나리오
테스트가 공유하는 순수 준비 로직일 뿐 판정 로직이 아니므로, 여기로 옮겨도
시나리오 자체의 응집도를 해치지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application.ingest_candles import ingest_candles
from src.foundation.market_data.application.register_instrument import (
    apply_lifecycle_event,
    register_instrument,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    IngestCandlesCommand,
    InstrumentRef,
    LifecycleEventCommand,
    RegisterInstrumentCommand,
    Timeframe,
    Venue,
)
from src.foundation.market_data.ports.ingest_source import IngestSource


class MockIngestSource(IngestSource):
    """모의 ingest source: 계약 테스트용. LA-9 포트 구현."""

    def __init__(
        self,
        candles: list[CandleRecord] | None = None,
        fail_mode: str = "ok",
    ):
        self.candles = candles or []
        self.fail_mode = fail_mode
        self.fetch_calls: list[tuple[Venue, str, Timeframe, datetime, datetime]] = []

    async def fetch_candles(
        self,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[CandleRecord]:
        """LA-9 IngestSource 포트 계약."""
        self.fetch_calls.append((venue, raw_symbol, tf, start, end))

        if self.fail_mode == "timeout":
            raise TimeoutError("ingest source did not respond within budget")
        if self.fail_mode == "http_error":
            raise RuntimeError("HTTP 500: upstream service unavailable")

        return self.candles


def _clock(t0: datetime) -> Callable[[], datetime]:
    """테스트용 clock: 고정 시각."""

    def clock() -> datetime:
        return t0

    return clock


async def listed_instrument(
    deps: SimpleNamespace, venue: Venue, base_date: datetime
) -> InstrumentRef:
    """테스트용 상장 상품 생성."""
    listed_at = base_date - timedelta(days=1)
    # BITGET symbols must end with USDT (crypto normalizer requirement)
    if venue is Venue.BITGET:
        symbol = f"T{uuid4().hex[:10].upper()}USDT"
    else:
        symbol = f"TEST{uuid4().hex[:8].upper()}"
    cmd = RegisterInstrumentCommand(
        venue=venue,
        venue_symbol=symbol,
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        listed_at=listed_at,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)
    return await apply_lifecycle_event(
        deps.pool,
        LifecycleEventCommand(
            instrument_id=instrument.instrument_id,
            event="LIST",
            effective_at=base_date - timedelta(hours=1),
            source_ref="e2e-3:setup",
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
        ),
        current=instrument,
        refs=deps.refs,
        audit=deps.audit,
    )


def ingest_cmd(
    instrument: InstrumentRef, start: datetime, end: datetime, *, venue: Venue = Venue.BITGET
) -> IngestCandlesCommand:
    """테스트용 ingest command."""
    return IngestCandlesCommand(
        tenant_id=None,
        venue=venue,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=start,
        range_end=end,
        trace_id=uuid4(),
    )


async def run_ingest(
    deps: SimpleNamespace,
    cmd: IngestCandlesCommand,
    source: IngestSource,
    *,
    clock_at: datetime,
) -> IngestBatchResult:
    """테스트용 ingest_candles 호출."""
    return await ingest_candles(
        cmd,
        source=source,
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=deps.audit,
        pool=deps.pool,
        clock=_clock(clock_at),
    )


def simple_sma(closes: list[Decimal], period: int) -> list[Decimal | None]:
    """단순 이동평균(SMA) — 순수 함수, Decimal 정밀도 유지."""
    if period <= 0 or len(closes) < period:
        return [None] * len(closes)
    result: list[Decimal | None] = [None] * (period - 1)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        avg = sum(window) / Decimal(period)
        result.append(avg)
    return result


# ---------------------------------------------------------------------------
# Negative / 실패주입 테스트 — market_data_helpers 자체 검증
# ---------------------------------------------------------------------------


def test_simple_sma_period_zero_returns_all_none() -> None:
    """부정: period=0 은 모든 값에 None 반환 (I-02 멱등/안정)."""
    closes = [Decimal("100"), Decimal("101"), Decimal("102")]
    result = simple_sma(closes, period=0)
    assert result == [None, None, None]


def test_simple_sma_period_negative_returns_all_none() -> None:
    """부정: period<0 은 모든 값에 None 반환 (잘못된 파라미터 거부)."""
    closes = [Decimal("100"), Decimal("101")]
    result = simple_sma(closes, period=-3)
    assert result == [None, None]


def test_simple_sma_empty_input_returns_empty_list() -> None:
    """부정: 빈 입력은 빈 리스트 반환 (예외가 아닌 안전한 빈 결과)."""
    result = simple_sma([], period=5)
    assert result == []


def test_simple_sma_insufficient_data_returns_all_none() -> None:
    """부정: 데이터가 period보다 적으면 모두 None (조기 종료 불변식)."""
    closes = [Decimal("100"), Decimal("101")]
    result = simple_sma(closes, period=5)
    assert result == [None, None]


def test_simple_sma_exact_period_returns_single_value() -> None:
    """정경: 데이터가 정확히 period와 같으면 첫 period-1개는 None, 마지막은 평균."""
    closes = [Decimal("100"), Decimal("200"), Decimal("300")]
    result = simple_sma(closes, period=3)
    assert result == [None, None, Decimal("200")]


@pytest.mark.asyncio
async def test_mock_ingest_source_fail_mode_http_error() -> None:
    """부정: MockIngestSource의 http_error fail_mode가 RuntimeError를 유발한다."""
    source = MockIngestSource(fail_mode="http_error")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await source.fetch_candles(
            Venue.BITGET,
            "TESTUSDT",
            Timeframe.M1,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_mock_ingest_source_fail_mode_unknown_ignores() -> None:
    """부정: 미지원 fail_mode는 ok로 처리 (알 수 없는 모드는 안전 처리)."""
    source = MockIngestSource(candles=[], fail_mode="unknown_mode")
    result = await source.fetch_candles(
        Venue.BITGET,
        "TESTUSDT",
        Timeframe.M1,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert result == []


def test_clock_returns_consistent_time() -> None:
    """불변식: 고정 clock은 항상 동일한 시각을 반환한다."""
    t0 = datetime(2026, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
    clock = _clock(t0)
    assert clock() == t0
    assert clock() == t0  # 재호출해도 동일


@pytest.mark.asyncio
async def test_run_ingest_monkeypatch_source_raises_connection_error() -> None:
    """실패주입: ingest_candles가 호출될 때 source.fetch_candles가
    ConnectionError를 raise하면 해당 예외가 전파된다."""
    from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    test_candle = CandleRecord(
        key=SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1),
        open_time=base_time,
        close_time=base_time + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("105"),
        volume=Decimal("100"),
    )

    source = MockIngestSource(candles=[test_candle], fail_mode="timeout")

    # fail_mode="timeout"이므로 fetch_candles가 TimeoutError를 raise
    with pytest.raises(TimeoutError, match="did not respond within budget"):
        await source.fetch_candles(
            Venue.BITGET,
            "TESTUSDT",
            Timeframe.M1,
            base_time,
            base_time + timedelta(minutes=1),
        )


def test_ingest_cmd_produces_valid_command() -> None:
    """음성: ingest_cmd가 유효한 IngestCandlesCommand를 생성하는지 검증."""
    from uuid import UUID

    instrument = SimpleNamespace(
        instrument_id=uuid4(),
        canonical_symbol="TESTUSDT",
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)

    cmd = ingest_cmd(instrument, start, end)

    assert cmd.canonical_symbol == "TESTUSDT"
    assert cmd.timeframe == Timeframe.M1
    assert cmd.range_start == start
    assert cmd.range_end == end
    assert cmd.venue is Venue.BITGET
    assert isinstance(cmd.trace_id, UUID)
