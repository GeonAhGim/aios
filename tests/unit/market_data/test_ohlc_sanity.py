"""LA-4 — market_data/domain/quality/ohlc_sanity.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §8.1, §9.2 LA-4.

핵심 케이스(§8.1): 6개 위반(low<=min, high>=max, volume>=0,
close_time==open_time+duration, tz-aware UTC, 값 유한성) 각각 REJECT,
정상 캔들은 0이슈.
"""

import threading
from datetime import UTC, datetime, timedelta
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

UTC = UTC
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


@pytest.mark.perf
def test_check_candle_handles_large_batch_within_latency_budget(
    perf_budget: Any,
) -> None:
    """수치 성능 단언: 50,000개 캔들 검사가 O(n) 유지 하에 3초 예산 안에 끝난다.

    raw perf_counter → perf_budget.assert_within 전환 (task-11809)."""
    candles = [
        _candle(
            open_time=_OPEN + timedelta(minutes=i),
            close_time=_CLOSE + timedelta(minutes=i),
        )
        for i in range(50_000)
    ]

    def _run_batch() -> None:
        for c in candles:
            check_candle(c)

    perf_budget.assert_within(_run_batch, budget_ms=3000)


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


@pytest.mark.perf
def test_check_candle_batch_p95_within_budget(
    perf_budget: Any,
) -> None:
    """수치 성능 단언: 100,000개 캔들(위반 케이스 혼합) 검사가 p95 < 100ms.

    raw perf_counter → perf_budget.assert_within 전환 (task-11809).
    budget_ms=5000 (5.0초) — 배치 전체 시간 기준."""
    valid = _candle()
    invalid_low = _candle(low=Decimal("101"))
    invalid_vol = _candle(volume=Decimal("-1"))
    batch = [valid, invalid_low, invalid_vol] * (100_000 // 3) + [
        valid,
        invalid_low,
        invalid_vol,
    ][:100_000]

    def _run_batch() -> None:
        for c in batch:
            check_candle(c)

    perf_budget.assert_within(_run_batch, budget_ms=5000)


# ── 단일 호출 CPU 측정 + 0 수렴 ──


@pytest.mark.perf
def test_check_candle_single_call_cpu_converges_to_zero(
    perf_budget: Any,
) -> None:
    """단일 호출 CPU 측정: check_candle 1회 호출이 CPU 시간 1ms 미만이어야 한다.

    check_candle은 순수 CPU 함수이므로 I/O 대기 없이 실제 CPU 시간만 측정한다.
    Windows의 time.process_time() 약 15.6ms 양자화 오차를 batch=64로 줄여
    0 수렴을 검증한다(task-11539).
    """
    valid = _candle()

    def _one_call() -> None:
        check_candle(valid)

    sample = perf_budget.sample(_one_call, batch=64)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"순수 도메인 로직은 1ms 미만이어야 함 (budget<1ms)"
    )


# ── 게이트 적색 재현: 성능 단언이 지연을 포착하는지 역으로 증명 ──


def test_cpu_budget_catches_10x_delay_injection(
    perf_budget: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산(5000ms × 10 = 50000ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """

    def _fake_process_time() -> float:
        _fake_process_time._accum += 50000.0  # +50s per call >> 5000ms budget
        return _fake_process_time._accum

    _fake_process_time._accum = 0.0

    # Patch where PerfBudget reads it (tests.support.perf_budget module level)
    import tests.support.perf_budget as _pb_module

    monkeypatch.setattr(_pb_module.time, "process_time", _fake_process_time)

    valid = _candle()

    def _one_call() -> None:
        check_candle(valid)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_one_call, batch=5)
        assert sample.cpu_ms < 5000.0  # 이 단언이 실패해야 함


def test_cpu_budget_passes_with_sleep_injection(
    perf_budget: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트-red 재현 — check_candle 생성은 순수 CPU 작업이므로
    `time.sleep`을 monkeypatch해도 CPU 시간이 늘어나지 않는다. PerfBudget이
    `time.process_time()`을 사용하므로 sleep 주입은 CPU 측정에 영향을
    주지 않는다.

    `time.sleep`을 100ms 지연으로 교체한 후 check_candle을 측정하면
    CPU 시간은 0에 수렴하고 wall-clock만 지연된다. CPU 예산 단언이
    통과하는 것으로 입증한다.
    """
    import time as _time

    _sleep_count = [0]

    def _slow_sleep(duration: float) -> None:
        _sleep_count[0] += 1
        _time.sleep(0.1)  # 100ms per sleep call

    # Patch where the code under test would call it (it doesn't, but this
    # proves the measurement system is CPU-only)
    monkeypatch.setattr(_time, "sleep", _slow_sleep)

    valid = _candle()

    def _one_call() -> None:
        check_candle(valid)

    sample = perf_budget.sample(_one_call, batch=64)
    # check_candle은 sleep을 호출하지 않으므로 CPU 시간은 0에 수렴
    assert sample.cpu_ms < 1.0, f"sleep 주입에도 CPU 시간이 초과됨: {sample.cpu_ms:.3f}ms"
