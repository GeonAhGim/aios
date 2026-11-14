"""E2E-4 깊이 하한 보강(D1->D2) -- 음성/실패주입/게이트 적색/성능 보조 테스트.

기존 `test_e2e_4_script_to_backtest_report.py`를 수정하지 않고, 부족한 증거만
보조 파일로 더한다.

- negative 3종: 문법 오류, 빈 봉 데이터, 시리즈 길이 불일치.
- 실패 주입: nan 신호가 na=True로 처리됨.
- 게이트 적색: corrupted signal이 정상과 다른 결과를 내는지 단언.
- 성능: compile->execute->backtest best-of-3 cpu 예산 + 10x 가상 지연 적색 재현.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.script.artifact.compile import compile_source
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.series import Series
from src.core.script.runtime.values import ScriptRuntimeError
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import bool_from_series
from src.foundation.market_data.api import CandleColumns
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.e2e.test_e2e_4_script_to_backtest_report import (
    _CLOSES,
    REGISTRY_VERSION,
    SCRIPT_SOURCE,
    _candle_columns,
    _zero_cost_config,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
)

# ---- 음성 테스트 #1 -- 문법 오류 스크립트 ----


def test_invalid_syntax_rejected_at_compile() -> None:
    """음성: 문법 오류가 있는 스크립트는 컴파일 단계에서 SCRIPT_SYNTAX로 거부된다."""
    invalid_source = (
        "input close: series<float> = 0\nsignal go_long = close > close[1]\ninvalid line here @#$\n"
    )

    with pytest.raises(Exception) as exc_info:
        compile_source(invalid_source, registry_version=REGISTRY_VERSION)

    assert exc_info.value.code == "SCRIPT_SYNTAX"


# ---- 음성 테스트 #2 -- 빈 봉 데이터 ----


def test_empty_candles_rejected_by_backtest() -> None:
    """음성: 봉 데이터가 비어 있으면 백테스트가 조용히 통과하지 않고
    ValueError를.raise한다."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    result = execute(
        compiled.ir,
        bar_count=1,
        inputs={"close": Series.of_floats([0.0])},
    )
    go_long = bool_from_series(result.signals["go_long"])
    go_flat = bool_from_series(result.signals["go_flat"])
    vector_signal = VectorSignal(entries=go_long, exits=go_flat, quantity=Decimal("1"))
    config = _zero_cost_config()

    # 빈 봉 데이터: ts가 비어 있음
    empty_columns = CandleColumns(
        ts=[],
        open=[],
        high=[],
        low=[],
        close=[],
        volume=[],
        quote_volume=[],
    )

    with pytest.raises(ValueError):
        run_vector_backtest(
            config,
            empty_columns,
            vector_signal,
            timeframe=Timeframe.M1,
            initial_cash=Decimal("100000"),
        )


# ---- 음성 테스트 #3 -- series 길이 불일치 ----


def test_series_length_mismatch_rejected_at_execute() -> None:
    """음성: 입력 series 길이가 bar_count와 다르면 execute가 거부한다."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    # close series는 3개지만 bar_count=6으로 요청 -> 불일치
    with pytest.raises(ScriptRuntimeError):
        execute(
            compiled.ir,
            bar_count=6,
            inputs={"close": Series.of_floats(float(c) for c in _CLOSES[:3])},
        )


# ---- 실패 주입 #1 -- None(결측) 신호 ----


def test_none_signal_handled_as_na() -> None:
    """실패 주입: signal series에 None(결측) 값을 주입하면 bool_from_series가
    None을 정상 신호로 변환하지 않음을 확인 -- na가 True여야 한다."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    result = execute(
        compiled.ir,
        bar_count=len(_CLOSES),
        inputs={"close": Series.of_floats(float(c) for c in _CLOSES)},
    )
    # signal values는 tuple이므로 새 Series 생성: bar 2를 None으로 설정
    raw_values = list(result.signals["go_long"].values)
    raw_values[2] = None
    na_series = Series(tuple(raw_values))

    go_long = bool_from_series(na_series)
    # None 값이 있는 bar는 na=True여야 함 (신호 발화 아님)
    assert bool(go_long.na[2]) is True, "None이 섞인 값은 na=True여야 함"


# ---- 게이트 적색 재현 -- corrupted signal breaks backtest identity ----


def test_corrupted_signal_breaks_backtest_fill_count() -> None:
    """게이트 적색 재현: signal 값을 모두 True로 망가뜨리면 정상적 신호와
    다른 결과(체결 수 증가)가 나온다 -- 단언이 실제로 차이를 잡는지 확인."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    result = execute(
        compiled.ir,
        bar_count=len(_CLOSES),
        inputs={"close": Series.of_floats(float(c) for c in _CLOSES)},
    )

    # 정상 신호로 백테스트
    go_long_normal = bool_from_series(result.signals["go_long"])
    go_flat_normal = bool_from_series(result.signals["go_flat"])
    normal_signal = VectorSignal(
        entries=go_long_normal, exits=go_flat_normal, quantity=Decimal("1")
    )
    config = _zero_cost_config()
    columns = _candle_columns()
    normal_bt = run_vector_backtest(
        config, columns, normal_signal, timeframe=Timeframe.M1, initial_cash=Decimal("100000")
    )
    normal_fill_count = len(normal_bt.fills)

    # corrupted: go_long을 모두 True, go_flat을 모두 False로 망가뜨림
    all_true_series = Series.of_floats([1.0] * len(_CLOSES))
    all_false_series = Series.of_floats([0.0] * len(_CLOSES))
    go_long_corrupt = bool_from_series(all_true_series)
    go_flat_corrupt = bool_from_series(all_false_series)
    corrupt_signal = VectorSignal(
        entries=go_long_corrupt, exits=go_flat_corrupt, quantity=Decimal("1")
    )
    corrupt_bt = run_vector_backtest(
        config, columns, corrupt_signal, timeframe=Timeframe.M1, initial_cash=Decimal("100000")
    )
    corrupt_fill_count = len(corrupt_bt.fills)

    # corrupted 신호는 진입/청산 없이 진입만 반복하므로 체결 수가 달라야 한다.
    assert corrupt_fill_count != normal_fill_count, (
        f"corrupted signal이 fill 수를 바꿔야 함: "
        f"normal={normal_fill_count}, corrupt={corrupt_fill_count}"
    )


# ---- 성능 단언 + 게이트 적색 재현 (10x 가상 지연) ----

# 실측 약 5ms의 100배 이상 여유 (로컬 best-of-3 CPU 5ms, 30배=150ms)
# CI 환경 추가 마진 포함
_PERF_BUDGET_MS = 500.0


def _measure_pipeline(perf_budget: PerfBudget, *, delay_s: float = 0.0):
    """compile -> execute -> bool_from_series -> run_vector_backtest 전체 파이프라인."""

    def _run() -> object:
        compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
        result = execute(
            compiled.ir,
            bar_count=len(_CLOSES),
            inputs={"close": Series.of_floats(float(c) for c in _CLOSES)},
        )
        go_long = bool_from_series(result.signals["go_long"])
        go_flat = bool_from_series(result.signals["go_flat"])
        vector_signal = VectorSignal(entries=go_long, exits=go_flat, quantity=Decimal("1"))
        return run_vector_backtest(
            _zero_cost_config(),
            _candle_columns(),
            vector_signal,
            timeframe=Timeframe.M1,
            initial_cash=Decimal("100000"),
        )

    call = virtual_delayed_sync(_run, delay_s) if delay_s else _run
    return perf_budget.best_of(call, n=3, warmup=1)


@pytest.mark.perf
def test_pipeline_perf_budget(perf_budget: PerfBudget) -> None:
    """성능 단언: compile->execute->backtest 전체 파이프라인 best-of-3 cpu가
    예산 미만."""
    reset_virtual_totals()
    sample = _measure_pipeline(perf_budget)
    assert sample.cpu_ms < _PERF_BUDGET_MS, perf_budget.describe(sample, budget_ms=_PERF_BUDGET_MS)


@pytest.mark.perf
def test_pipeline_gate_red_with_10x_injection(perf_budget: PerfBudget) -> None:
    """게이트 적색 재현: 예산 10배 가상 지연을 주입하면 같은 예산 단언이 적색이 된다."""
    reset_virtual_totals()
    sample = _measure_pipeline(perf_budget, delay_s=_PERF_BUDGET_MS * 10 / 1000)
    assert not (sample.cpu_ms < _PERF_BUDGET_MS)
    reset_virtual_totals()
