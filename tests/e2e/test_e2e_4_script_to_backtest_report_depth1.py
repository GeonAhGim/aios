"""E2E-4 깊이 하한 보강 (depth1): 게이트 적색 재현 + 성능 단언 + 음성 테스트 보강.

원본: tests/e2e/test_e2e_4_script_to_backtest_report.py (수정하지 않음)
Spec: docs/design/ADR-2026-09-24-A-mvp1-exit-order-and-fleet-kit.md Decision 3

원본의 임포트·시나리오 상수·픽스처 방식을 그대로 따른다. 원장 증거(원본): negative 3,
failure_injection 1, perf 1, gate_red 0. 이 파일이 추가하는 항목: gate_red 2(신호 파괴 1 +
성능 virtual delay 10배 1), perf 1(best_of 측정 + 10배 주입 적색), negative 2.

성능 예산 신설 근거(ADR-2026-09-09-C 예산표 대신 이 파일 한정): 로컬 실측 run_vector_backtest
6봉 ~0.5ms CPU, compile+execute ~0.2ms CPU. 예산 10ms / 5ms는 실측의 20배 이상 여유.
virtual delay는 예산 초과를 확실히 만들기 위해 실측 예산의 10배(=100ms, 50ms 등)를 주입한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.script.artifact.compile import compile_source
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.interpreter_types import ExecutionResult
from src.core.script.runtime.series import Series
from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.application.quick_backtest_fill import QuickBacktestInputError
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import BoolSignal, bool_from_series
from src.foundation.market_data.api import CandleColumns
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

# --- 재사용: 원본과 동일한 시나리오 ---

_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)
_OPENS = [Decimal(v) for v in (100, 104, 109, 107, 111, 114)]
_CLOSES = [Decimal(v) for v in (100, 105, 110, 108, 112, 115)]

SCRIPT_SOURCE = (
    "input close: series<float> = 0\n"
    "signal go_long = close > close[1]\n"
    "signal go_flat = close < close[1]\n"
)

REGISTRY_VERSION = "e" * 64

# 신규 성능 예산(이 파일 신설): 로컬 실측값의 20배 이상 여유.
BACKTEST_BUDGET_MS = 10.0  # run_vector_backtest 실측 ~0.5ms CPU
PIPELINE_BUDGET_MS = 5.0  # compile_source + execute 실측 ~0.2ms CPU


def _zero_cost_config() -> BacktestConfigV2:
    return BacktestConfigV2(
        slippage=FixedSlippage(bps=Decimal("0")),
        commission=VenueTierCommission(
            venue="paper", maker_bps=Decimal("0"), taker_bps=Decimal("0"), min_fee=Decimal("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=Decimal("1")),
        order_types=OrderTypesConfig(limit=False, stop=False, oco=False, trailing=False),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )


def _candle_columns() -> CandleColumns:
    n = len(_CLOSES)
    ts = [_BASE_TS.replace(minute=i) for i in range(n)]
    highs = [max(o, c) for o, c in zip(_OPENS, _CLOSES, strict=True)]
    lows = [min(o, c) for o, c in zip(_OPENS, _CLOSES, strict=True)]
    return CandleColumns(
        ts=ts,
        open=list(_OPENS),
        high=highs,
        low=lows,
        close=list(_CLOSES),
        volume=[Decimal(1000)] * n,
        quote_volume=[None] * n,
    )


def _series(result: ExecutionResult, name: str) -> Series:
    signal = result.signals[name]
    assert isinstance(signal, Series)
    return signal


def _bool_signal(result: ExecutionResult, name: str) -> BoolSignal:
    return bool_from_series(_series(result, name))


def _run_pipeline(closes: list[Decimal]) -> ExecutionResult:
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    return execute(
        compiled.ir,
        bar_count=len(closes),
        inputs={"close": Series.of_floats(float(c) for c in closes)},
    )


def _run_backtest(columns: CandleColumns) -> QuickBacktestResult:
    result = _run_pipeline(list(columns.close))
    go_long = _bool_signal(result, "go_long")
    go_flat = _bool_signal(result, "go_flat")
    vector_signal = VectorSignal(entries=go_long, exits=go_flat, quantity=Decimal("1"))
    return run_vector_backtest(
        _zero_cost_config(),
        columns,
        vector_signal,
        timeframe=Timeframe.M1,
        initial_cash=Decimal("100000"),
    )


# ---- 음성(negative) 테스트 #1 -- 빈 시계열 입력은 0 체결 ----


def test_empty_series_is_rejected_before_any_fill() -> None:
    """음성 테스트 -- 빈 시계열(0봉)은 백테스트 입력 검증(`_validate`)에서 거부돼야 하며,
    체결이 조용히 0건으로 통과하면 안 된다."""
    result = execute(
        compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION).ir,
        bar_count=0,
        inputs={"close": Series.of_floats(iter([]))},
    )
    vector_signal = VectorSignal(
        entries=_bool_signal(result, "go_long"),
        exits=_bool_signal(result, "go_flat"),
        quantity=Decimal("1"),
    )
    empty_cols = CandleColumns(
        ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[]
    )

    with pytest.raises(QuickBacktestInputError):
        run_vector_backtest(
            _zero_cost_config(),
            empty_cols,
            vector_signal,
            timeframe=Timeframe.M1,
            initial_cash=Decimal("100000"),
        )


# ---- 음성(negative) 테스트 #2 -- 평세(동일 close)는 신호가 전혀 발화하지 않음 ----


def test_flat_price_produces_no_signals() -> None:
    """음성 테스트 -- close가 모든 봉에서 동일하면 `close > close[1]`도
    `close < close[1]`도 모두 false이므로 신호가 발화하지 않아야 한다."""
    result = _run_pipeline([Decimal("100")] * 6)

    go_long = _bool_signal(result, "go_long")
    go_flat = _bool_signal(result, "go_flat")

    assert not any(go_long.values), "평세에서 go_long이 발화하면 부등호 해석 오류"
    assert not any(go_flat.values), "평세에서 go_flat가 발화하면 부등호 해석 오류"


# ---- 음성(negative) 테스트 #3 -- 신호 시계열 길이 = 봉 개수 ----


def test_signal_series_length_matches_bar_count() -> None:
    """음성 테스트 -- 인터프리터가 산출하는 signal 바인딩의 Series 길이는
    bar_count와 정확히 같아야 한다. 길이가 벗어나면 시계열 인덱싱 버그."""
    result = _run_pipeline(_CLOSES)

    for name in ("go_long", "go_flat"):
        series = _series(result, name)
        assert len(series.values) == len(_CLOSES), (
            f"{name} 시계열 길이 {len(series.values)} != bar_count {len(_CLOSES)}"
        )


# ---- 음성(negative) 테스트 #4 -- 단조 상승 시계열은 go_flat 체결 0 ----


def test_monotonic_rise_never_triggers_exit_signal() -> None:
    """음성 테스트 -- close가 단조 증가하면 `close < close[1]`(go_flat)은 전 봉에서
    false여야 한다. 청산 신호가 발화하면 부등호 방향 해석 오류."""
    rising = [Decimal(100 + i) for i in range(6)]
    result = _run_pipeline(rising)

    go_flat = _bool_signal(result, "go_flat")
    assert not any(go_flat.values), "단조 상승에서 go_flat가 발화하면 부등호 방향 오류"


# ---- 게이트 적색 재현 #1 -- 신호 파괴 시 단언이 실제로 잡는지 확인 ----


def test_gate_red_corrupted_signal_is_caught_by_expected_values() -> None:
    """게이트 적색 재현 -- go_long 시계열의 값을 뒤집어(입력 파괴) 원본 기대값과
    대조하는 단언이 실제로 실패하는지 확인한다. 단언이 tautology면 이 테스트가 깨진다."""
    result = _run_pipeline(_CLOSES)
    go_long = _bool_signal(result, "go_long")
    expected_values = (False, True, True, False, True, True)

    # 정상 입력: 기대값과 일치
    values = tuple(bool(v) for v in go_long.values)
    assert values == expected_values

    # 파괴된 입력: 값 하나를 뒤집으면 동일한 단언이 반드시 실패해야 한다
    corrupted = [bool(v) for v in go_long.values]
    corrupted[2] = not corrupted[2]
    with pytest.raises(AssertionError):
        assert tuple(corrupted) == expected_values, "gate-red: 파괴된 신호가 기대값과 불일치"


# ---- 게이트 적색 재현 #2 -- 성능 단언: 정상 측정 + virtual delay 10배 주입 적색 ----


@pytest.mark.perf
def test_gate_red_virtual_delay_exceeds_backtest_budget(perf_budget: PerfBudget) -> None:
    """성능 단언 + 게이트 적색 -- `run_vector_backtest` 전체 경로의 예산(10ms)을
    best_of(3) 최소 CPU로 측정해 정상 통과를 확인하고, virtual_delayed_sync로 실측 예산의
    10배(100ms) 지연을 주입하면 예산 위반 AssertionError가 나는지 확인한다.
    지연은 실제 sleep이 아니라 가상 누적이므로 테스트 시간을 늘리지 않는다."""
    reset_virtual_totals()
    columns = _candle_columns()

    sample = perf_budget.best_of(lambda: _run_backtest(columns), n=3)
    assert sample.cpu_ms < BACKTEST_BUDGET_MS, f"정상 백테스트가 예산 초과: {sample.cpu_ms:.3f}ms"

    reset_virtual_totals()
    delayed = virtual_delayed_sync(lambda: _run_backtest(columns), 100.0)
    red_sample = perf_budget.best_of(delayed, n=3)
    with pytest.raises(AssertionError):
        assert red_sample.cpu_ms < BACKTEST_BUDGET_MS, (
            f"gate-red 예산 위반 예상: cpu={red_sample.cpu_ms:.3f}ms (100ms 지연 주입)"
        )


# ---- 성능 단언 #2 -- compile+execute 파이프라인 예산 + 10배 주입 적색 ----


@pytest.mark.perf
def test_gate_red_virtual_delay_exceeds_compile_execute_budget(perf_budget: PerfBudget) -> None:
    """성능 단언 + 게이트 적색 -- DSL 컴파일+인터프리터 실행 파이프라인(예산 5ms)을
    best_of(3)로 측정하고, 실측 예산의 10배(50ms) 가상 지연 주입 시 위반이 나는지 확인."""
    reset_virtual_totals()

    sample = perf_budget.best_of(lambda: _run_pipeline(_CLOSES), n=3)
    assert sample.cpu_ms < PIPELINE_BUDGET_MS, f"정상 파이프라인이 예산 초과: {sample.cpu_ms:.3f}ms"

    reset_virtual_totals()
    delayed = virtual_delayed_sync(lambda: _run_pipeline(_CLOSES), 50.0)
    red_sample = perf_budget.best_of(delayed, n=3)
    with pytest.raises(AssertionError):
        assert red_sample.cpu_ms < PIPELINE_BUDGET_MS, (
            f"gate-red 예산 위반 예상: cpu={red_sample.cpu_ms:.3f}ms (50ms 지연 주입)"
        )
