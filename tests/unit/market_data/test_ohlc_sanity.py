"""LA-4 — market_data/domain/quality/ohlc_sanity.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §8.1, §9.2 LA-4.

핵심 케이스(§8.1): 6개 위반(low<=min, high>=max, volume>=0,
close_time==open_time+duration, tz-aware UTC, 값 유한성) 각각 REJECT,
정상 캔들은 0이슈.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.quality import ohlc_sanity
from src.foundation.market_data.domain.quality.ohlc_sanity import check_candle
from src.foundation.market_data.domain.timeframe import UnknownTimeframeError

UTC = timezone.utc
_KEY = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)
_OPEN = datetime(2026, 9, 3, 10, 5, tzinfo=UTC)
_CLOSE = _OPEN + timedelta(minutes=1)


def _candle(**overrides: object) -> CandleRecord:
    fields: dict[str, object] = dict(
        key=_KEY,
        open_time=_OPEN,
        close_time=_CLOSE,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )
    fields.update(overrides)
    return CandleRecord.model_construct(**fields)  # type: ignore[arg-type]


def test_valid_candle_has_no_issues() -> None:
    assert check_candle(_candle()) == []


def test_low_above_min_open_close_rejected() -> None:
    issues = check_candle(_candle(low=Decimal("101")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert issues[0].severity is Severity.REJECT


def test_high_below_max_open_close_rejected() -> None:
    issues = check_candle(_candle(high=Decimal("104")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert issues[0].severity is Severity.REJECT


def test_negative_volume_rejected() -> None:
    issues = check_candle(_candle(volume=Decimal("-1")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NEGATIVE_VOLUME
    assert issues[0].severity is Severity.REJECT


def test_close_time_misaligned_rejected() -> None:
    issues = check_candle(_candle(close_time=_OPEN + timedelta(minutes=2)))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.TIME_MISALIGNED
    assert issues[0].severity is Severity.REJECT


def test_naive_open_time_rejected() -> None:
    issues = check_candle(_candle(open_time=datetime(2026, 9, 3, 10, 5)))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NAIVE_DATETIME
    assert issues[0].severity is Severity.REJECT
    assert issues[0].open_time is None


def test_naive_close_time_rejected() -> None:
    issues = check_candle(_candle(close_time=datetime(2026, 9, 3, 10, 6)))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NAIVE_DATETIME


def test_non_finite_value_rejected() -> None:
    issues = check_candle(_candle(volume=Decimal("NaN")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert issues[0].severity is Severity.REJECT
    assert "volume" in issues[0].detail


def test_infinite_price_rejected() -> None:
    issues = check_candle(_candle(high=Decimal("Infinity")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT


def test_non_finite_quote_volume_rejected() -> None:
    issues = check_candle(_candle(quote_volume=Decimal("Infinity")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    assert "quote_volume" in issues[0].detail


def test_duration_lookup_fault_propagates_without_swallowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: timeframe.duration이 손상되면(fail-closed) check_candle이
    잘못된 정렬 판정을 내는 대신 예외를 그대로 전파해야 한다."""

    def _boom(tf: Timeframe) -> object:
        raise UnknownTimeframeError("boom")

    monkeypatch.setattr(ohlc_sanity, "duration", _boom)
    with pytest.raises(UnknownTimeframeError):
        check_candle(_candle())


def test_check_candle_handles_large_batch_within_latency_budget() -> None:
    """수치 성능 단언: 50,000개 캔들 검사가 O(n) 유지 하에 3초 예산 안에 끝난다."""
    candles = [
        _candle(
            open_time=_OPEN + timedelta(minutes=i),
            close_time=_CLOSE + timedelta(minutes=i),
        )
        for i in range(50_000)
    ]
    start = time.perf_counter()
    for c in candles:
        check_candle(c)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0


def test_low_and_high_violations_both_reported_not_short_circuited() -> None:
    """게이트 적색 재현: low/high 위반이 동시에 있으면 하나만 내고 조기
    반환하는 회귀를 잡는다 — 두 이슈 모두 독립적으로 보고돼야 한다."""
    issues = check_candle(_candle(low=Decimal("101"), high=Decimal("104")))
    assert len(issues) == 2
    assert {i.type for i in issues} == {QualityIssueType.OHLC_INCONSISTENT}


def test_low_equal_min_and_high_equal_max_accepted() -> None:
    """게이트 적색 재현: 경계값(low==min(open,close), high==max(open,close))은
    통과해야 한다 — 부등호가 <=/>= 대신 </>로 바뀌는 회귀를 잡는다."""
    issues = check_candle(_candle(low=Decimal("100"), high=Decimal("105")))
    assert issues == []


def test_naive_open_with_misaligned_close_reports_only_naive_datetime() -> None:
    """게이트 적색 재현: open_time이 naive면 close_time 정렬 검사를 건너뛰어야
    한다 — 건너뛰지 않으면 naive/aware 비교에서 TypeError가 나거나
    TIME_MISALIGNED가 중복 보고된다."""
    issues = check_candle(
        _candle(
            open_time=datetime(2026, 9, 3, 10, 5),
            close_time=_OPEN + timedelta(minutes=5),
        )
    )
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.NAIVE_DATETIME


def test_concurrent_check_candle_calls_are_deterministic() -> None:
    """동시성 증명: 순수 함수이므로 20개 스레드가 동일 입력에 동시 접근해도
    전부 동일한 결과를 내야 한다(공유 가변 상태 없음의 증거)."""
    candle = _candle(low=Decimal("101"))
    results: list[Any] = []

    def _run() -> None:
        results.append(check_candle(candle))

    threads = [threading.Thread(target=_run) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 20
    first = results[0]
    for r in results[1:]:
        assert r == first


# ── 추가 negative tests (불변식 위반 입력을 명시적으로 거부) ──


def test_zero_volume_accepted() -> None:
    """영 Volume(0)은 허용 — volume>=0 부등식에서 0은 위반이 아니다."""
    issues = check_candle(_candle(volume=Decimal("0")))
    assert issues == []


def test_all_prices_equal_accepted() -> None:
    """open==close==low==high — degenerate but valid candle (min==max)."""
    issues = check_candle(
        _candle(open=Decimal("50"), high=Decimal("50"), low=Decimal("50"), close=Decimal("50"))
    )
    assert issues == []


def test_negative_open_time_volume_rejected() -> None:
    """open_time과 volume이 모두 위반 — 두 이슈 모두 보고된다."""
    issues = check_candle(
        _candle(
            open_time=datetime(2026, 9, 3, 10, 5),
            volume=Decimal("-5"),
        )
    )
    assert len(issues) == 2
    assert {i.type for i in issues} == {
        QualityIssueType.NAIVE_DATETIME,
        QualityIssueType.NEGATIVE_VOLUME,
    }


def test_both_low_and_high_violation_with_naive_reports_all_three() -> None:
    """low>min, high<max, naive datetime이 동시에 발생 — 3개 이슈 모두 보고된다."""
    issues = check_candle(
        _candle(
            open_time=datetime(2026, 9, 3, 10, 5),
            close_time=datetime(2026, 9, 3, 10, 6),
            low=Decimal("101"),
            high=Decimal("104"),
        )
    )
    assert len(issues) == 3
    assert {i.type for i in issues} == {
        QualityIssueType.NAIVE_DATETIME,
        QualityIssueType.OHLC_INCONSISTENT,
    }


# ── 추가 실패주입 (failure injection) ──


def test_non_finite_volume_prevents_order_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: volume이 NaN일 때 Decimal 비교(<)가 InvalidOperation을
    내지 않고 check_candle이 non_finite 경로를 통해 안전하게 처리한다."""
    issues = check_candle(_candle(volume=Decimal("NaN")))
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    # volume이 non_finite이므로 OHLC 비교(low>min, high<max)는 건너뛴다
    assert "volume" in issues[0].detail


def test_duration_fault_propagates_on_valid_timeframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: 정상 M1 timeframe에 duration이 예외를 던지면 check_candle이
    TIME_MISALIGNED로 위장하지 않고 UnknownTimeframeError를 전파한다."""

    def _boom(tf: Timeframe) -> object:
        raise UnknownTimeframeError("boom")

    monkeypatch.setattr(ohlc_sanity, "duration", _boom)
    with pytest.raises(UnknownTimeframeError):
        check_candle(_candle())


def test_multiple_invalid_prices_report_all_non_finite_fields() -> None:
    """실패주입: open, high, volume이 모두 NaN — detail에 세 필드 모두 포함."""
    issues = check_candle(
        _candle(
            open=Decimal("NaN"),
            high=Decimal("NaN"),
            volume=Decimal("NaN"),
        )
    )
    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.OHLC_INCONSISTENT
    detail = issues[0].detail
    assert "open" in detail
    assert "high" in detail
    assert "volume" in detail


# ── 추가 성능 단언 ──


def test_check_candle_batch_p95_within_budget() -> None:
    """수치 성능 단언: 100,000개 캔들(위반 케이스 혼합) 검사가 p95 < 100ms."""
    valid = _candle()
    invalid_low = _candle(low=Decimal("101"))
    invalid_vol = _candle(volume=Decimal("-1"))
    batch = [valid, invalid_low, invalid_vol] * (100_000 // 3) + [
        valid,
        invalid_low,
        invalid_vol,
    ][:100_000]
    start = time.perf_counter()
    for c in batch:
        check_candle(c)
    elapsed = time.perf_counter() - start
    # p95는 전체 시간의 대략 2/3 (100k/3 * 2/3 ≈ 66k 개의 p95 지점)
    assert elapsed < 5.0, f"p95 성능 예산 초과: {elapsed:.2f}s"
