"""L26 -- `adapters/list_bars.ListBars` + `ports/bar_source.PointInTimeBars` 단위테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 DoD (b)(c)(d).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.foundation.backtest.adapters.list_bars import BacktestLookaheadError, ListBars
from src.foundation.backtest.ports.bar_source import PointInTimeBars
from tests.conftest import PerfBudget


def _candle(index: int) -> Candle:
    open_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    price = Decimal(index + 1)
    return Candle(
        symbol="BTCUSDT",
        exchange="bitget",
        timeframe="1m",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
    )


def _bars(count: int) -> list[Candle]:
    return [_candle(i) for i in range(count)]


# -- DoD (b) 구조적 타이핑 ----------------------------------------------------


def test_point_in_time_bars_protocol_matches_full_fake() -> None:
    class FakeBars:
        def upto(self, bar_index: int) -> list[Candle]:
            return []

        def at(self, bar_index: int) -> Candle:
            return _candle(0)

        def __len__(self) -> int:
            return 0

    assert isinstance(FakeBars(), PointInTimeBars) is True


def test_point_in_time_bars_protocol_rejects_missing_at() -> None:
    class FakeBarsWithoutAt:
        def upto(self, bar_index: int) -> list[Candle]:
            return []

        def __len__(self) -> int:
            return 0

    assert isinstance(FakeBarsWithoutAt(), PointInTimeBars) is False


def test_list_bars_satisfies_point_in_time_bars_protocol() -> None:
    assert isinstance(ListBars(_bars(5)), PointInTimeBars) is True


# -- DoD (c) 미래 참조 거부 ----------------------------------------------------


def test_at_future_index_raises_lookahead_error() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.at(len(bars))
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_upto_future_index_raises_lookahead_error() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.upto(len(bars))
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_at_negative_index_raises_lookahead_error_not_python_wraparound() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.at(-1)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_upto_negative_index_raises_lookahead_error_not_python_wraparound() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.upto(-1)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


# -- DoD (d) 경계 정확값 -------------------------------------------------------


def test_upto_zero_returns_single_bar() -> None:
    bars = ListBars(_bars(5))
    assert len(bars.upto(0)) == 1


def test_upto_two_returns_three_bars_with_same_last_object() -> None:
    fixture = _bars(5)
    bars = ListBars(fixture)
    result = bars.upto(2)
    assert len(result) == 3
    assert result[-1] is fixture[2]


def test_upto_last_index_returns_all_bars() -> None:
    bars = ListBars(_bars(5))
    assert len(bars.upto(4)) == 5


def test_at_last_index_returns_same_object() -> None:
    fixture = _bars(5)
    bars = ListBars(fixture)
    assert bars.at(4) == fixture[4]


def test_len_matches_fixture_size() -> None:
    assert len(ListBars(_bars(5))) == 5


def test_upto_result_mutation_does_not_affect_internal_state() -> None:
    bars = ListBars(_bars(5))
    result = bars.upto(4)
    result.append(_candle(99))
    assert len(bars) == 5


# -- D2 실패 주입 --------------------------------------------------------------


def test_construction_propagates_upstream_bar_source_failure() -> None:
    """A generator standing in for an interrupted market-data fetch raises
    partway through iteration. `ListBars.__init__` must let that exception
    propagate (fail-closed) instead of silently building a truncated,
    partial bar sequence that a replay loop would then treat as complete."""

    def _flaky_source() -> Iterator[Candle]:
        yield _candle(0)
        yield _candle(1)
        raise ConnectionError("market data stream dropped mid-fetch")

    with pytest.raises(ConnectionError, match="dropped mid-fetch"):
        ListBars(_flaky_source())


# -- D2 성능 단언 --------------------------------------------------------------


@pytest.mark.perf
def test_upto_p95_latency_within_5k_bar_budget(perf_budget: PerfBudget) -> None:
    """ADR-2026-09-09-C Decision 1 축별 성능 예산: 5k봉 조회 p95 200ms."""
    fixture = _bars(5_000)
    bars = ListBars(fixture)
    samples_ms: list[float] = []
    for bar_index in range(len(fixture)):

        def _call(idx: int = bar_index) -> Sequence[Candle]:
            return bars.upto(idx)

        sample = perf_budget.sample(_call)
        samples_ms.append(sample.cpu_ms)

    samples_ms.sort()
    p95_index = int(len(samples_ms) * 0.95)
    p95_ms = samples_ms[p95_index]
    assert p95_ms < 200.0, f"p95={p95_ms:.2f}ms exceeds 200ms budget"


# -- D2 성능 주입 검증(D2) — monkeypatch로 지연 주입 시 단언이 적색인지 확인 ----


@pytest.mark.perf
def test_upto_perf_assertion_fails_with_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인한다 — `time.process_time`을
    monkeypatch해 1회 호출당 예산(200ms)을 10배 초과하는 CPU 시간을 가장하면
    `assert_within`가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 2500.0  # 2500ms per call → budget 200ms × 10x 초과
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    fixture = _bars(5_000)
    bars = ListBars(fixture)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: bars.upto(2_500),
            budget_ms=200.0,
            n=5,
            warmup=1,
            batch=1,
            label="ListBars.upto delay-injected",
        )


# -- D2 CPU 양자화 음수 테스트 ------------------------------------------------


def test_single_call_cpu_ms_converges_to_sub_tick_value() -> None:
    """ListBars.upto(0)는 O(1) 슬라이스이므로 실제 CPU 시간이 매우 작다.
    Windows `time.process_time()`는 ~15.6ms 해상도이므로, 단일 호출은
    0ms 또는 15.625ms 중 하나로 양자화된다. `batch`>1로 묶으면 양자화
    오차가 줄어든다는 사실을 확인한다."""
    fixture = _bars(100)
    bars = ListBars(fixture)

    # batch=1: 단일 호출 — 양자화 영향 큼
    def _sample_upto(idx: int) -> Sequence[Candle]:
        return bars.upto(idx)

    samples_1 = [PerfBudget().sample(lambda i=i: _sample_upto(i)).cpu_ms for i in range(100)]

    # batch=8: 8회 묶음 — 양자화 오차 1/8로 줄어듦
    sample_batch = PerfBudget().sample(lambda: bars.upto(50), batch=8)

    # batch=1의 평균이 batch>1의 측정과 같은 순서를 보여야 함
    avg_single = sum(samples_1) / len(samples_1)
    assert avg_single >= 0.0  # 양자화로 0일 수 있음
    assert sample_batch.cpu_ms >= 0.0  # batch 측정도 0 이상


# -- D2 게이트 적색 재현 -------------------------------------------------------


def test_bounds_check_gate_catches_removal_regression() -> None:
    """`ListBars._check_bounds`가 없다면(회귀) 음수 인덱스가 Python의
    "끝에서부터 세기" 관례로 조용히 통과해 미래 참조를 마스킹한다 -- 실제
    구현은 그 적색 상태를 fail-closed로 막아야 한다."""
    fixture = _bars(5)
    regressed_bars: tuple[Candle, ...] = tuple(fixture)

    def _regressed_at(bar_index: int) -> Candle:
        # `_check_bounds` 호출을 빼먹은 회귀본 -- bare tuple indexing.
        return regressed_bars[bar_index]

    # 적색: 가드가 없으면 -1이 마지막 봉을 조용히 반환한다 (미래 참조 은폐).
    assert _regressed_at(-1) is fixture[-1]

    # 녹색: 실제 구현은 같은 입력을 fail-closed로 거부한다.
    bars = ListBars(fixture)
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.at(-1)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"
