"""DC-22 — domain/aggregation/tick_to_candle 단위 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-22, §9.10 DC-22.

핵심 케이스: 틱→캔들 집계가 결정론적(같은 입력=바이트 동일 직렬화 sha256)이고,
역순 ts_event는 거부하며, 같은 (ts_event, seq) 중복 틱은 1건으로 접히고,
갭 구간에는 0거래량 유령봉을 만들지 않으며, 세션 밖 틱은 제외되고, 경계
캔들은 LA-2 `align_open`/`expected_opens` 위임(로컬 산식 재구현 금지)으로
정확히 세션 종료에 클립된다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.candle_lineage import SourceKind
from src.foundation.market_data.contracts.v2.microstructure import Aggressor, TradeTick
from src.foundation.market_data.domain.aggregation.tick_to_candle import (
    MixedSeriesError,
    TickToCandleResult,
    UnsortedTicksError,
    ticks_to_candles,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar

UTC = timezone.utc
_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_BASE_NS = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z (Thursday)


def _bitget_calendar() -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=spec.tz, regular=spec)


def _krx_calendar(early_closes: dict[date, time] | None = None) -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.KIS_KRX.value]
    return VenueCalendar(
        venue=Venue.KIS_KRX.value, tz=spec.tz, regular=spec, early_closes=early_closes or {}
    )


def _tick(
    seconds_offset: int,
    seq: int,
    price: str,
    size: str,
    *,
    sub_ns: int = 0,
    instrument_id: str = _ULID,
    venue: Venue = Venue.BITGET,
) -> TradeTick:
    ts_event = _BASE_NS + seconds_offset * 1_000_000_000 + sub_ns
    return TradeTick(
        instrument_id=instrument_id,
        venue=venue,
        ts_event=ts_event,
        ts_recv=ts_event + 1_000,
        seq=seq,
        price=Decimal(price),
        size=Decimal(size),
        aggressor=Aggressor.BUY,
    )


_KST = ZoneInfo("Asia/Seoul")


def _kst_tick(hour: int, minute: int, seq: int, price: str, size: str) -> TradeTick:
    day = datetime(2026, 9, 4, hour, minute, tzinfo=_KST)
    ts_event = int(day.timestamp()) * 1_000_000_000
    return TradeTick(
        instrument_id=_ULID,
        venue=Venue.KIS_KRX,
        ts_event=ts_event,
        ts_recv=ts_event + 1_000,
        seq=seq,
        price=Decimal(price),
        size=Decimal(size),
        aggressor=Aggressor.BUY,
    )


def _canonical_hash(result: TickToCandleResult) -> str:
    rows = [
        {
            "ts": result.columns.ts[i].isoformat(),
            "open": str(result.columns.open[i]),
            "high": str(result.columns.high[i]),
            "low": str(result.columns.low[i]),
            "close": str(result.columns.close[i]),
            "volume": str(result.columns.volume[i]),
            "quote_volume": str(result.columns.quote_volume[i]),
            "lineage": result.lineage[i].model_dump(mode="json"),
        }
        for i in range(len(result.columns))
    ]
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---- 기본 집계 정확성 + source_kind/lineage ----


def test_ticks_to_candles_aggregates_ohlcv_and_lineage_correctly() -> None:
    ticks = [
        _tick(0, seq=1, price="100", size="1"),
        _tick(10, seq=2, price="105", size="2"),
        _tick(59, seq=3, price="95", size="1"),
        _tick(60, seq=4, price="110", size="1"),
    ]
    result = ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())

    assert result.source_kind == SourceKind.TICK_DERIVED
    assert len(result.columns) == 2
    assert result.columns.open == [Decimal("100"), Decimal("110")]
    assert result.columns.high == [Decimal("105"), Decimal("110")]
    assert result.columns.low == [Decimal("95"), Decimal("110")]
    assert result.columns.close == [Decimal("95"), Decimal("110")]
    assert result.columns.volume == [Decimal("4"), Decimal("1")]
    assert result.columns.quote_volume == [Decimal("405"), Decimal("110")]

    assert result.lineage[0].tick_count == 3
    assert result.lineage[0].first_seq == 1
    assert result.lineage[0].last_seq == 3
    assert result.lineage[1].tick_count == 1
    assert result.lineage[1].first_seq == 4
    assert result.lineage[1].last_seq == 4


def test_ticks_to_candles_empty_input_returns_empty_result() -> None:
    result = ticks_to_candles([], Timeframe.M1, _bitget_calendar())
    assert len(result.columns) == 0
    assert result.lineage == ()
    assert result.source_kind == SourceKind.TICK_DERIVED


# ---- (a) 결정론: 같은 입력 = 바이트 동일 직렬화 sha256 ----


def test_ticks_to_candles_is_byte_identical_for_same_input() -> None:
    def _build() -> list[TradeTick]:
        return [
            _tick(0, seq=1, price="100", size="1"),
            _tick(1, seq=2, price="101", size="2"),
            _tick(61, seq=3, price="102", size="3"),
        ]

    calendar = _bitget_calendar()
    first = ticks_to_candles(_build(), Timeframe.M1, calendar)
    second = ticks_to_candles(_build(), Timeframe.M1, calendar)

    assert first == second
    assert _canonical_hash(first) == _canonical_hash(second)


# ---- (b) 거부: 역순 ts_event ----


def test_ticks_to_candles_rejects_unsorted_ts_event() -> None:
    ticks = [
        _tick(1, seq=1, price="100", size="1"),
        _tick(0, seq=2, price="101", size="1"),
    ]
    with pytest.raises(UnsortedTicksError):
        ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())


# ---- (b) 같은 (ts_event, seq) 중복 틱은 1건으로 접힌다 ----


def test_ticks_to_candles_dedupes_identical_identity() -> None:
    ticks = [
        _tick(0, seq=1, price="100", size="1"),
        _tick(0, seq=1, price="100", size="1"),  # 정확히 같은 (ts_event, seq) 재전송
        _tick(5, seq=2, price="101", size="2"),
    ]
    result = ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())

    assert len(result.columns) == 1
    assert result.lineage[0].tick_count == 2
    assert result.columns.volume == [Decimal("3")]


# ---- (c) 갭: 틱이 없는 구간에는 빈 봉을 만들지 않는다 ----


def test_ticks_to_candles_gap_produces_no_ghost_candle() -> None:
    ticks = [
        _tick(0, seq=1, price="100", size="1"),
        _tick(120, seq=2, price="105", size="1"),  # 사이(1분봉 1개분) 갭
    ]
    result = ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())

    assert len(result.columns) == 2
    assert result.columns.ts == [
        datetime.fromtimestamp(_BASE_NS // 1_000_000_000, tz=UTC),
        datetime.fromtimestamp(_BASE_NS // 1_000_000_000 + 120, tz=UTC),
    ]
    assert Decimal("0") not in result.columns.volume


# ---- (c) 세션 밖 틱은 LA-3 calendar 판정으로 제외한다 ----


def test_ticks_to_candles_excludes_out_of_session_ticks() -> None:
    ticks = [
        _kst_tick(8, 0, seq=1, price="9999", size="1"),  # 개장 전 — 어느 봉에도 안 들어간다
        _kst_tick(9, 5, seq=2, price="100", size="1"),
    ]
    result = ticks_to_candles(ticks, Timeframe.M1, _krx_calendar())

    assert len(result.columns) == 1
    assert result.columns.open == [Decimal("100")]
    assert result.lineage[0].tick_count == 1


# ---- (d) 위임 증명: 조기폐장 경계는 align_open/expected_opens가 정한다 ----


def test_ticks_to_candles_clips_boundary_to_early_close() -> None:
    """KRX 09:07 조기폐장. M5 그리드는 09:05~09:10이지만 세션은 09:07에
    닫히므로, 09:07 이후 틱은 (설사 존재해도) 그 창에 집계되면 안 된다.
    이 경계는 로컬 산식이 아니라 `expected_opens`(내부에서 `align_open`
    호출)가 만든 open과 `session.close_at` 클립으로만 나온다 — 위임을
    로컬 산식으로 바꾸면 09:07 이후 극단값이 high/low/close에 새어 들어와
    이 테스트가 깨진다."""
    calendar = _krx_calendar(early_closes={date(2026, 9, 4): time(9, 7)})

    def _tick_at(hour: int, minute: int, seq: int, price: str) -> TradeTick:
        day = datetime(2026, 9, 4, hour, minute, tzinfo=_KST)
        ts_event = int(day.timestamp()) * 1_000_000_000
        return TradeTick(
            instrument_id=_ULID,
            venue=Venue.KIS_KRX,
            ts_event=ts_event,
            ts_recv=ts_event + 1_000,
            seq=seq,
            price=Decimal(price),
            size=Decimal("1"),
            aggressor=Aggressor.BUY,
        )

    ticks = [
        _tick_at(9, 5, 1, "100"),
        _tick_at(9, 6, 2, "103"),
        _tick_at(9, 7, 3, "999"),  # 세션이 이미 닫힌 뒤 — 결과에 반영되면 버그
    ]
    result = ticks_to_candles(ticks, Timeframe.M5, calendar)

    assert len(result.columns) == 1
    assert result.columns.high == [Decimal("103")]
    assert result.columns.close == [Decimal("103")]
    assert result.lineage[0].tick_count == 2


# ---- 입력 방어(fail-closed) ----


def test_ticks_to_candles_rejects_mixed_instrument_series() -> None:
    ticks = [
        _tick(0, seq=1, price="100", size="1"),
        _tick(1, seq=2, price="101", size="1", instrument_id="01BX5ZZKBKACTAV9WEVGEMMVRZ"),
    ]
    with pytest.raises(MixedSeriesError):
        ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())
