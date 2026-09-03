"""LA-20 — kis_ingest_source 테스트(httpx.MockTransport만 사용, 실키 없음).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-20.
DoD: LA-15 bitget_ingest_source와 동일 포트 시그니처, [start, end) 필터링,
KRX 정규장 세션(LA-3 VenueCalendar)으로 장중 갭과 장외 시간대를 구분
(장 마감 구간 캔들 없음 → 갭 아님, 장중 결측 → 갭), 휴장일은 세션 자체가
없어 갭 판정 대상에서 빠진다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.adapters.kis_ingest_source import (
    KisIngestSource,
    UnsupportedTimeframeError,
    UnsupportedVenueError,
)
from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe, Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.timeframe import expected_opens

_TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_adapter(handler) -> KISAdapter:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        return handler(request)

    transport = httpx.MockTransport(route)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _krx_calendar(*, holidays: frozenset[date] = frozenset()) -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.KIS_KRX.value]
    return VenueCalendar(venue=Venue.KIS_KRX.value, tz=spec.tz, regular=spec, holidays=holidays)


def _intraday_row(open_time: datetime) -> dict:
    ot_utc = open_time.astimezone(timezone.utc)
    return {
        "stck_bsop_date": ot_utc.strftime("%Y%m%d"),
        "stck_cntg_hour": ot_utc.strftime("%H%M%S"),
        "stck_oprc": "70000",
        "stck_hgpr": "70100",
        "stck_lwpr": "69900",
        "stck_prpr": "70050",
        "cntg_vol": "10",
    }


def _daily_row(day: str) -> dict:
    return {
        "stck_bsop_date": day,
        "stck_oprc": "70000",
        "stck_hgpr": "70100",
        "stck_lwpr": "69900",
        "stck_clpr": "70050",
        "acml_vol": "1000",
    }


async def test_fetch_candles_maps_daily_ohlc_and_filters_range() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output2": [_daily_row("20260901"), _daily_row("20260902"), _daily_row("20260903")],
            },
        )

    adapter = _make_adapter(handler)
    source = KisIngestSource(adapter)
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, tzinfo=timezone.utc)

    candles = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)

    assert [c.open_time for c in candles] == [
        datetime(2026, 9, 2, tzinfo=timezone.utc),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    ]
    assert candles[0].close == Decimal("70050")
    assert candles[0].key.venue is Venue.KIS_KRX


async def test_fetch_candles_rejects_unsupported_venue() -> None:
    source = KisIngestSource(_make_adapter(lambda r: httpx.Response(200, json=_TOKEN_RESPONSE)))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    with pytest.raises(UnsupportedVenueError):
        await source.fetch_candles(Venue.BITGET, "005930", Timeframe.D1, start, start)


async def test_fetch_candles_rejects_unsupported_timeframe() -> None:
    source = KisIngestSource(_make_adapter(lambda r: httpx.Response(200, json=_TOKEN_RESPONSE)))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    with pytest.raises(UnsupportedTimeframeError):
        await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.M5, start, start)


async def test_fetch_candles_rejects_naive_datetime() -> None:
    source = KisIngestSource(_make_adapter(lambda r: httpx.Response(200, json=_TOKEN_RESPONSE)))
    naive = datetime(2026, 9, 2)
    with pytest.raises(ValueError, match="tz-aware"):
        await source.fetch_candles(
            Venue.KIS_KRX, "005930", Timeframe.D1, naive, datetime(2026, 9, 3, tzinfo=timezone.utc)
        )


async def test_krx_intraday_missing_candle_inside_session_is_gap() -> None:
    day = date(2026, 9, 4)  # 금요일, 정규 거래일(휴장/조기폐장 없음)
    full_session = _krx_calendar().sessions_for(day)[0]
    start = full_session.open_at
    end = start + timedelta(minutes=10)
    window = SessionWindow(open_at=start, close_at=min(end, full_session.close_at), kind="REGULAR")
    expected = expected_opens(start, end, Timeframe.M1, [window])
    missing = {expected[3], expected[7]}
    present = [ot for ot in expected if ot not in missing]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output2": [_intraday_row(ot) for ot in present]}
        )

    source = KisIngestSource(_make_adapter(handler))
    candles = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.M1, start, end)
    assert len(candles) == len(present)

    issues = detect_gaps(candles, Timeframe.M1, [window])
    assert {i.open_time for i in issues} == missing


async def test_krx_missing_candle_after_market_close_is_not_gap() -> None:
    day = date(2026, 9, 4)  # 금요일, 정규 거래일 — 마감 15:30 KST 이후 결측
    full_session = _krx_calendar().sessions_for(day)[0]
    start = full_session.close_at - timedelta(minutes=5)  # 15:25 KST
    end = start + timedelta(minutes=10)  # 15:35 KST — 마감 이후까지 요청
    window = SessionWindow(open_at=start, close_at=min(end, full_session.close_at), kind="REGULAR")
    expected = expected_opens(start, end, Timeframe.M1, [window])  # 15:25~15:29만(마감 전)

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [_intraday_row(ot) for ot in expected]
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output2": rows})

    source = KisIngestSource(_make_adapter(handler))
    candles = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.M1, start, end)
    assert len(candles) == len(expected)  # 마감 이후 캔들은 애초에 기대 집합에 없다

    issues = detect_gaps(candles, Timeframe.M1, [window])
    assert issues == []


def test_krx_holiday_has_no_session_so_no_gap() -> None:
    day = date(2026, 9, 4)
    cal = _krx_calendar(holidays=frozenset({day}))
    sessions = cal.sessions_for(day)
    assert sessions == []  # 휴장일은 세션 자체가 없다(LA-3)
    assert detect_gaps([], Timeframe.M1, sessions) == []
