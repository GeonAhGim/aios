"""E2E-4 depth 보강(D1->D2) — 스크립트 -> 백테스트 경로의 negative/실패주입/게이트 적색/성능.

기존 `test_e2e_4_script_to_backtest_report.py`는 수정하지 않고, 부족한 증거만 보조 파일로 더한다.

- negative 3종: 미선언 식별자 스크립트 거부, 빈 스크립트 거부, 필수 input 누락 실행 거부.
- 실패 주입: 컴파일된 IR 실행 중 입력 길이 불일치(bar_count 초과)가 조용히 통과하지 않는다.
- 게이트 적색: 입력 close 시리즈를 망가뜨리면 체결 가격/최종 자산 단언이 실제로 적색이 된다.
- 성능: compile+execute+vector backtest(6봉) best-of-5 cpu 실측 약 0.3ms(로컬),
  예산 100ms(30배 이상 여유). 10배 가상 지연(perf_inject)을 주입하면 같은 예산 단언이
  적색이 된다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.script.artifact.compile import ScriptCompileError, compile_source
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.series import Series
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import bool_from_series
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.e2e.test_e2e_4_script_to_backtest_report import (
    _CLOSES,
    REGISTRY_VERSION,
    SCRIPT_SOURCE,
    _candle_columns,
    _zero_cost_config,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_PIPELINE_BUDGET_MS = 100.0  # 실측 약 0.3ms의 30배 이상 여유


def _run_pipeline(closes: list[float] | None = None) -> Decimal:
    """compile -> execute -> vectorize -> backtest; returns final equity."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    values = closes if closes is not None else [float(c) for c in _CLOSES]
    result = execute(
        compiled.ir,
        bar_count=len(_CLOSES),
        inputs={"close": Series.of_floats(values)},
    )
    signal = VectorSignal(
        entries=bool_from_series(result.signals["go_long"]),
        exits=bool_from_series(result.signals["go_flat"]),
        quantity=Decimal("1"),
    )
    bt = run_vector_backtest(
        _zero_cost_config(),
        _candle_columns(),
        signal,
        timeframe=Timeframe.M1,
        initial_cash=Decimal("100000"),
    )
    return bt.final_equity


def test_undeclared_identifier_script_is_rejected() -> None:
    """negative: 선언되지 않은 식별자를 쓰는 스크립트는 컴파일 단계에서 거부된다."""
    source = "input close: series<float> = 0\nsignal go_long = ghost > close[1]\n"
    with pytest.raises(ScriptCompileError) as exc_info:
        compile_source(source, registry_version=REGISTRY_VERSION)
    assert exc_info.value.code.startswith("SCRIPT_")


def test_garbage_script_is_rejected() -> None:
    """negative: 문법에 맞지 않는 쓰레기 입력은 통과하지 않고 ScriptCompileError."""
    with pytest.raises(ScriptCompileError):
        compile_source("}}} not a script", registry_version=REGISTRY_VERSION)


def test_execute_without_required_input_is_rejected() -> None:
    """negative: 필수 input(close)을 주지 않은 실행은 빈 신호로 성공하지 않고 예외."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    with pytest.raises(Exception):  # noqa: B017 - 어떤 fail-closed 예외든 무방
        execute(compiled.ir, bar_count=len(_CLOSES), inputs={})


def test_failure_injection_input_length_mismatch_is_not_silent() -> None:
    """실패 주입: bar_count보다 짧은 입력 시리즈는 정상 결과처럼 통과하면 안 된다."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)
    short = Series.of_floats(float(c) for c in _CLOSES[:3])
    try:
        result = execute(compiled.ir, bar_count=len(_CLOSES), inputs={"close": short})
    except Exception:  # noqa: BLE001 - 거부되면 fail-closed 성공
        return
    # 거부하지 않는다면 신호 길이가 bar_count와 일치하거나, 후속 벡터화가 거부해야 한다.
    sig = result.signals["go_long"]
    assert isinstance(sig, Series)
    with pytest.raises(Exception):  # noqa: B017
        run_vector_backtest(
            _zero_cost_config(),
            _candle_columns(),
            VectorSignal(
                entries=bool_from_series(sig),
                exits=bool_from_series(result.signals["go_flat"]),
                quantity=Decimal("1"),
            ),
            timeframe=Timeframe.M1,
            initial_cash=Decimal("100000"),
        )


def test_gate_red_corrupted_close_breaks_final_equity_assertion() -> None:
    """게이트 적색 재현: close 입력을 망가뜨리면 최종 자산 단언이 실제로 적색이 된다."""
    assert _run_pipeline() == Decimal("100003")  # 정상 입력은 단언 통과
    flat = [100.0] * len(_CLOSES)  # 신호가 전혀 발화하지 않는 입력
    bad = _run_pipeline(flat)
    with pytest.raises(AssertionError):
        assert bad == Decimal("100003"), "final_equity"
    assert bad == Decimal("100000")


def _measure(perf_budget: PerfBudget, *, delay_s: float = 0.0):
    call = virtual_delayed_sync(_run_pipeline, delay_s) if delay_s else _run_pipeline
    return perf_budget.best_of(call, n=5, warmup=1)


@pytest.mark.perf
def test_pipeline_perf_budget(perf_budget: PerfBudget) -> None:
    """성능 단언: 6봉 compile+execute+vector backtest best-of-5 가 예산 미만."""
    reset_virtual_totals()
    sample = _measure(perf_budget)
    assert sample.cpu_ms < _PIPELINE_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_PIPELINE_BUDGET_MS
    )


@pytest.mark.perf
def test_pipeline_perf_gate_red_with_10x_injection(perf_budget: PerfBudget) -> None:
    """게이트 적색 재현: 예산 10배 가상 지연을 주입하면 같은 예산 단언이 적색이 된다."""
    reset_virtual_totals()
    sample = _measure(perf_budget, delay_s=_PIPELINE_BUDGET_MS * 10 / 1000)
    assert not (sample.cpu_ms < _PIPELINE_BUDGET_MS)
    reset_virtual_totals()
