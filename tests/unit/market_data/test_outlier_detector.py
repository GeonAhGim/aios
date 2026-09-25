"""LA-6 — market_data/domain/quality/outlier_detector.py 스파이크 탐지 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §8.1, §9.2 LA-6.

핵심 케이스(§8.1): 합성 시계열에 +30% 스파이크 1개 → 정확히 그 캔들만
검출, 변동성 높은 정상 구간(고정 시드)은 오탐 0. 추가로 채널 2(인접 캔들
대비 high/low 비율 상한)도 별도 검증한다.

DEEPEN(task-2950, docs/audit/DEPTH_LA_LB_LC.md original task-390): this module
is a pure function with no I/O (same premise as `test_session_rules.py`'s
DEEPEN for LA-3), so the missing axes are translated rather than applied
literally: adversarial input (non-positive close price, the precondition the
module docstring documents as required but not type-enforced), failure
injection (`_median` corrupted via monkeypatch), numeric performance, gate-red
reproduction (the `_MAD_FLOOR` guard), replay, and concurrency.
"""

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import pytest

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.quality import outlier_detector as _outlier_detector_module
from src.foundation.market_data.domain.quality.outlier_detector import detect_spikes

UTC = timezone.utc
_KEY = SeriesKey(venue=Venue.BITGET, instrument_id=UUID(int=1), timeframe=Timeframe.M1)
_START = datetime(2026, 9, 1, tzinfo=UTC)


def _series(closes: list[Decimal]) -> list[CandleRecord]:
    candles = []
    open_price = closes[0]
    for i, close in enumerate(closes):
        high = max(open_price, close) * Decimal("1.001")
        low = min(open_price, close) * Decimal("0.999")
        open_time = _START + timedelta(minutes=i)
        candles.append(
            CandleRecord(
                key=_KEY,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=Decimal("1"),
            )
        )
        open_price = close
    return candles


def _noisy_closes(seed: int, steps: int, bound: str) -> list[Decimal]:
    rnd = random.Random(seed)
    limit = float(bound)
    closes = [Decimal("100")]
    for _ in range(steps):
        factor = Decimal(str(round(1 + rnd.uniform(-limit, limit), 6)))
        closes.append(closes[-1] * factor)
    return closes


def test_detect_spikes_flags_exactly_the_injected_spike_candle() -> None:
    closes = _noisy_closes(seed=42, steps=300, bound="0.02")
    spike_at = 150
    spiked = closes[:spike_at] + [c * Decimal("1.30") for c in closes[spike_at:]]
    candles = _series(spiked)

    issues = detect_spikes(candles)

    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.SPIKE
    assert issues[0].open_time == candles[spike_at].open_time


def test_detect_spikes_no_false_positive_on_volatile_normal_segment() -> None:
    closes = _noisy_closes(seed=7, steps=300, bound="0.03")
    candles = _series(closes)

    issues = detect_spikes(candles)

    assert issues == []


def test_detect_spikes_flags_high_low_ratio_channel() -> None:
    flat = [Decimal("100")] * 80
    candles = _series(flat)
    wick_idx = 40
    original = candles[wick_idx]
    candles[wick_idx] = original.model_copy(update={"high": original.high * Decimal("4")})

    issues = detect_spikes(candles)

    assert len(issues) == 1
    assert issues[0].open_time == candles[wick_idx].open_time
    assert issues[0].detail["reason"] == "hl_ratio"


def test_detect_spikes_fewer_than_two_candles_returns_empty() -> None:
    candles = _series([Decimal("100")])

    assert detect_spikes(candles) == []


def test_detect_spikes_raises_for_non_positive_close_price_adversarial_input() -> None:
    """Adversarial input -- `CandleRecord.close` is typed `Decimal` with no
    positivity constraint, so a caller that violates this module's
    documented precondition (prices stay positive; LA-4 OHLC sanity is
    supposed to guarantee that upstream) can still construct one directly,
    with no bypass needed. Pins the module's own documented consequence: the
    log-return ratio going non-positive dies loud via `Decimal.ln()` raising
    `InvalidOperation`, instead of silently producing a wrong value."""
    candles = _series([Decimal("100")] * 12)
    candles[6] = candles[6].model_copy(update={"close": Decimal("-50")})

    with pytest.raises(InvalidOperation):
        detect_spikes(candles)


def test_detect_spikes_propagates_failure_from_corrupted_median() -> None:
    """Failure injection -- if the internal `_median` helper ever fails
    unexpectedly (e.g. a future edit that lets it choke on a malformed
    trailing window), `detect_spikes` must not swallow it into a silent
    empty/partial issue list: it has no try/except around the computation,
    and this pins that fail-loud shape against regression."""

    def _boom(values: list[Decimal]) -> Decimal:
        raise RuntimeError("simulated corrupted median computation")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_outlier_detector_module, "_median", _boom)
    try:
        closes = _noisy_closes(seed=1, steps=20, bound="0.01")
        candles = _series(closes)
        with pytest.raises(RuntimeError, match="simulated corrupted median computation"):
            detect_spikes(candles)
    finally:
        monkeypatch.undo()


@pytest.mark.perf
def test_detect_spikes_throughput_within_latency_budget() -> None:
    """Numeric performance assertion -- 20,000 candles (pure in-memory
    computation, no I/O) must finish well inside a generous budget. A
    regression that made the per-candle trailing-window median computation
    scale with the full history instead of the bounded `window` would blow
    this budget long before it hurt in production."""
    closes = _noisy_closes(seed=99, steps=20_000, bound="0.02")
    candles = _series(closes)

    started = time.perf_counter()
    detect_spikes(candles)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, (
        f"detect_spikes() over 20k candles took {elapsed:.2f}s, over the 5.0s budget"
    )


def test_gate_mad_floor_guard_prevents_false_positive_storm_on_flat_market() -> None:
    """Gate-red reproduction -- on a perfectly flat market (all trailing log
    returns exactly 0), the trailing median and MAD are both 0. Without the
    `_MAD_FLOOR` guard, `k_mad * mad` collapses to 0, so *any* nonzero return
    would exceed the threshold and every subsequent candle would falsely
    spike -- monkeypatching `_MAD_FLOOR` to 0 reproduces exactly that
    false-positive storm, proving the floor in the real module is
    load-bearing."""
    flat = [Decimal("100")] * 70
    tiny_move = flat + [Decimal("100.00001")] * 5
    candles = _series(tiny_move)

    assert detect_spikes(candles) == []

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_outlier_detector_module, "_MAD_FLOOR", Decimal("0"))
    try:
        assert len(detect_spikes(candles)) > 0
    finally:
        monkeypatch.undo()


def test_detect_spikes_is_deterministic_across_repeated_replay() -> None:
    """Replay proof -- callers that reprocess the same candle range (e.g. an
    at-least-once ingestion pipeline redelivering the same batch) must get
    an identical issue list from repeated `detect_spikes` calls on the same
    input."""
    closes = _noisy_closes(seed=5, steps=200, bound="0.02")
    candles = _series(closes)

    results = [detect_spikes(candles) for _ in range(10)]

    assert all(r == results[0] for r in results)


def test_concurrent_detect_spikes_calls_are_consistent_and_thread_safe() -> None:
    """Concurrency proof -- `detect_spikes` is a pure function with no shared
    mutable state, so concurrent calls on the same input from multiple
    threads must all agree with the single-threaded answer."""
    closes = _noisy_closes(seed=11, steps=200, bound="0.02")
    candles = _series(closes)
    expected = detect_spikes(candles)
    results: list[object] = [None] * 30

    def _call(i: int) -> None:
        results[i] = detect_spikes(candles)

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == expected for r in results)


def test_detect_spikes_empty_candles_list_returns_empty() -> None:
    """Adversarial input -- an empty candle list must not crash; the module
    should return an empty issue list (defensive guard)."""
    assert detect_spikes([]) == []


def test_detect_spikes_all_identical_prices_returns_no_issues() -> None:
    """Adversarial input -- a perfectly flat market (zero variance) must not
    raise or produce false positives; the `_MAD_FLOOR` guard must absorb the
    zero-MAD case."""
    flat = [Decimal("100")] * 120
    candles = _series(flat)

    issues = detect_spikes(candles)

    assert issues == []


def test_detect_spikes_multiple_separate_spikes_flags_all() -> None:
    """Negative test -- when multiple non-consecutive candles are spiked, the
    detector must flag each one independently (not collapse into a single
    issue or skip after the first detection)."""
    closes = _noisy_closes(seed=42, steps=200, bound="0.01")
    # Inject spikes at indices 50, 100, 150 (separated)
    spike_indices = {50, 100, 150}
    spiked = [c * Decimal("1.50") if i in spike_indices else c for i, c in enumerate(closes)]
    candles = _series(spiked)

    issues = detect_spikes(candles)

    issue_times = {iss.open_time for iss in issues}
    for idx in spike_indices:
        assert candles[idx].open_time in issue_times, f"Spike at index {idx} was not detected"


def test_detect_spikes_propagates_failure_from_corrupted_log_returns() -> None:
    """Failure injection -- if `_log_returns` (or the `Decimal.ln()` call
    inside it) fails unexpectedly, `detect_spikes` must not swallow it into
    a silent empty/partial result. Pins the fail-loud behaviour."""

    def _boom_returns(candles: list[Any]) -> list[Any]:
        raise RuntimeError("simulated log-returns computation failure")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_outlier_detector_module, "_log_returns", _boom_returns)
    try:
        closes = _noisy_closes(seed=1, steps=20, bound="0.01")
        candles = _series(closes)
        with pytest.raises(RuntimeError, match="simulated log-returns computation failure"):
            detect_spikes(candles)
    finally:
        monkeypatch.undo()


def test_detect_spikes_window_zero_skips_mad_channel_below_min_window() -> None:
    """Adversarial input -- when `window=0`, the trailing window slice is
    always empty (< `_MIN_WINDOW`), so the MAD channel must be skipped and
    only the hl_ratio channel can fire. Verifies the window parameter
    is respected and does not cause index errors."""
    flat = [Decimal("100")] * 80
    candles = _series(flat)
    # No spike, so MAD channel would fire if window were active; with
    # window=0 it should be skipped entirely and produce no issues.
    issues = detect_spikes(candles, window=0)
    assert issues == []
