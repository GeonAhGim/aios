"""LA-20 — kis_ingest_source 테스트(httpx.MockTransport만 사용, 실키 없음).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-20.
DoD: LA-15 bitget_ingest_source와 동일 포트 시그니처, [start, end) 필터링,
KRX 정규장 세션(LA-3 VenueCalendar)으로 장중 갭과 장외 시간대를 구분
(장 마감 구간 캔들 없음 → 갭 아님, 장중 결측 → 갭), 휴장일은 세션 자체가
없어 갭 판정 대상에서 빠진다.

DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md#713)가 원 task-713을
D1로 판정한 4건을 아래에서 채운다: (1) failure-injection — MockTransport가
바디 레벨 오류/불완전 응답도 시뮬레이션한다. (2) 수치 성능 단언 —
`test_fetch_candles_repeated_calls_p95_latency_under_budget`. (3) 게이트
적색 재현 — `test_gate_red_when_range_filter_removed_existing_test_would_fail`.
(4) 적대적/replay/동시성 증명 — 각각 하단 3개 테스트.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.adapters.kis_ingest_source import (
    KisIngestSource,
    UnsupportedTimeframeError,
    UnsupportedVenueError,
    _to_candle_record,
)
from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe, Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
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


def _daily_row(day: str, *, close: str = "70050") -> dict:
    return {
        "stck_bsop_date": day,
        "stck_oprc": "70000",
        "stck_hgpr": "70100",
        "stck_lwpr": "69900",
        "stck_clpr": close,
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


# --- failure-injection --------------------------------------------------


async def test_fetch_candles_raises_retryable_error_on_venue_body_error() -> None:
    """실패주입: KIS가 바디 레벨 오류(rt_cd != '0')를 반환하면 빈 리스트로
    조용히 뭉개지 않고 RetryableExchangeError를 그대로 전파한다(fail-closed,
    oauth_client.py `_classify_body`/`_request` 계약)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "일시 오류"})

    source = KisIngestSource(_make_adapter(handler))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, tzinfo=timezone.utc)

    with pytest.raises(RetryableExchangeError):
        await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)


async def test_fetch_candles_raises_fatal_error_on_malformed_row() -> None:
    """실패주입: output2 행에 필수 필드(stck_clpr)가 빠지면 잘못 조립된
    캔들을 반환하지 않고 FatalExchangeError로 즉시 실패한다
    (market_data_mixin.get_ohlcv의 KeyError->FatalExchangeError 변환)."""
    bad_row = _daily_row("20260902")
    del bad_row["stck_clpr"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output2": [bad_row]})

    source = KisIngestSource(_make_adapter(handler))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, tzinfo=timezone.utc)

    with pytest.raises(FatalExchangeError):
        await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)


# --- 수치 성능 단언 ---------------------------------------------------------


@pytest.mark.perf
async def test_fetch_candles_repeated_calls_p95_latency_under_budget() -> None:
    """수치 성능 단언: 토큰 캐시(MonotonicTokenCache) 이후의 반복 조회는
    순수 인메모리 MockTransport 왕복이라, 원장 append p95 예산(task-489/
    LB-18, task-614/LC-17, 30ms)과 같은 자릿수 안에 있어야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output2": [_daily_row("20260902")]}
        )

    source = KisIngestSource(_make_adapter(handler))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 3, tzinfo=timezone.utc)
    await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)  # 토큰 캐시 예열

    n = 50
    budget_p95_sec = 0.03
    latencies: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        candles = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)
        latencies.append(time.perf_counter() - t0)
        assert candles

    latencies.sort()
    p95 = latencies[int(n * 0.95)]
    print(
        f"[LA-20 fetch_candles] n={n} p95={p95 * 1000:.2f}ms (budget<{budget_p95_sec * 1000:.0f}ms)"
    )
    assert p95 < budget_p95_sec, f"fetch_candles p95가 예산을 넘었습니다: {p95:.4f}s"


# --- 게이트 적색 재현 -------------------------------------------------------


async def test_gate_red_when_range_filter_removed_existing_test_would_fail(monkeypatch) -> None:
    """게이트 적색 재현: [start, end) 클라이언트측 필터(핵심 DoD, 모듈
    docstring)를 제거하는 회귀를 주입하면
    test_fetch_candles_maps_daily_ohlc_and_filters_range가 지키는 범위
    단언이 green에서 red로 뒤집힘을 이 자리에서 직접 재현한다."""

    async def _regressed_fetch_candles(
        self: KisIngestSource,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list:
        if venue is not Venue.KIS_KRX:
            raise UnsupportedVenueError(f"KisIngestSource는 KIS_KRX 전용: {venue!r}")
        if tf not in {Timeframe.M1, Timeframe.D1}:
            raise UnsupportedTimeframeError(f"KIS는 1d/1m만 지원: {tf!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("fetch_candles는 tz-aware datetime만 받는다")
        canonical = to_canonical(venue, raw_symbol)
        raw = await self._adapter.get_ohlcv(canonical, tf.value, limit=self._max_limit)
        # 회귀: [start, end) 필터를 빼먹고 어댑터가 준 캔들을 그대로 반환한다.
        return [_to_candle_record(c, tf) for c in raw]

    monkeypatch.setattr(KisIngestSource, "fetch_candles", _regressed_fetch_candles)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output2": [_daily_row("20260901"), _daily_row("20260902"), _daily_row("20260903")],
            },
        )

    source = KisIngestSource(_make_adapter(handler))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, tzinfo=timezone.utc)

    candles = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)

    with pytest.raises(AssertionError):
        assert [c.open_time for c in candles] == [
            datetime(2026, 9, 2, tzinfo=timezone.utc),
            datetime(2026, 9, 3, tzinfo=timezone.utc),
        ]


# --- 적대적/replay/동시성 증명 ----------------------------------------------


async def test_fetch_candles_rejects_adversarial_rows_outside_requested_window() -> None:
    """적대적 증명: 오동작·악성 venue가 요청 범위 밖 행(먼 과거/먼 미래)과
    중복 행(재전송 시도)을 정상 행 사이에 끼워 넣어도 [start, end) 필터가
    범위 밖은 모두 걸러내고 범위 안만(중복 포함, 디듀프는 이 어댑터의
    책임이 아니다) 통과시킨다."""
    rows = [
        _daily_row("19000101"),  # 아주 먼 과거 — 적대적/오작동 데이터
        _daily_row("20260902"),
        _daily_row("20991231"),  # 아주 먼 미래 — 적대적/오작동 데이터
        _daily_row("20260902"),  # 중복 행(재전송 시도)
        _daily_row("20260903"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output2": rows})

    source = KisIngestSource(_make_adapter(handler))
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 6, tzinfo=timezone.utc)  # limit=5, rows 전부 응답에 포함되도록

    candles = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)

    assert [c.open_time for c in candles] == [
        datetime(2026, 9, 2, tzinfo=timezone.utc),
        datetime(2026, 9, 2, tzinfo=timezone.utc),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    ]


async def test_fetch_candles_replay_is_idempotent_and_reuses_cached_token() -> None:
    """replay 증명: 동일 요청을 반복 재생해도 결과가 매번 동일하고(숨은
    가변 상태 없음), 토큰 재발급도 최초 1회로 끝난다(MonotonicTokenCache가
    반복 호출 사이에서 재사용됨을 증명)."""
    token_calls = 0

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth2/tokenP":
            token_calls += 1
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output2": [_daily_row("20260902")]}
        )

    transport = httpx.MockTransport(route)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=client
    )
    source = KisIngestSource(adapter)
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 3, tzinfo=timezone.utc)

    first = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)
    second = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)
    third = await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)

    assert first == second == third
    assert token_calls == 1, (
        f"replay 사이 토큰이 {token_calls}회 재발급됨 -- 캐시가 재사용되지 않음"
    )


async def test_fetch_candles_concurrent_symbols_no_cross_contamination_single_token_fetch() -> None:
    """동시성 증명: 서로 다른 종목을 같은 어댑터(토큰 캐시·락 공유) 위에서
    동시에 조회해도 결과가 섞이지 않고, 최초 동시 요청들 사이의 토큰 발급
    레이스는 `asyncio.Lock` 이중 확인 패턴(oauth_client.py `_ensure_token`)
    덕에 정확히 1회로 수렴한다."""
    token_calls = 0
    expected_close = {"005930": "70050", "000660": "55000"}

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth2/tokenP":
            token_calls += 1
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        symbol = request.url.params.get("FID_INPUT_ISCD")
        row = _daily_row("20260902", close=expected_close[symbol])
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output2": [row]})

    transport = httpx.MockTransport(route)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=client
    )
    source = KisIngestSource(adapter)
    start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    end = datetime(2026, 9, 3, tzinfo=timezone.utc)

    symbols = ["005930", "000660"] * 5  # 10개 동시 요청, 심볼 교차 배치
    results = await asyncio.gather(
        *[source.fetch_candles(Venue.KIS_KRX, s, Timeframe.D1, start, end) for s in symbols]
    )

    for symbol, candles in zip(symbols, results, strict=True):
        assert len(candles) == 1
        assert candles[0].close == Decimal(expected_close[symbol]), (
            f"{symbol} 요청 결과에 다른 종목 데이터가 섞였다"
        )

    assert token_calls == 1, f"동시 첫 호출인데도 토큰이 {token_calls}회 발급됨 -- 락 경합 증거"
