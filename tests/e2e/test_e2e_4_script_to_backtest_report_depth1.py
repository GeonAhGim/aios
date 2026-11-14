"""E2E-4 D2 보강: 음성 3종 이상, 실패 주입, 게이트 적색 재현, 성능 단언.

기존 `test_e2e_4_script_to_backtest_report.py`의 상수/헬퍼를 그대로 재사용한다.
앱 코드와 기존 단언·예산은 건드리지 않는다(DB 불필요 -- 컴파일->실행->벡터 백테스트 구간).

성능 예산 신설 근거: 2026-11-14 로컬 best-of-5(컴파일+실행+벡터 백테스트 6봉 1회 왕복)
실측 wall 0.6~0.8ms. 예산 `_PIPELINE_BUDGET_MS`는
실측의 1000배 이상(30배 이상 요건 충족) 여유이며 CI 경합을 포함한다. 컴파일 단계에
가상 지연 10배(예산 x10)를 주입하면 같은 단언이 적색이어야 한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.script.artifact.compile import ScriptCompileError, compile_source
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.series import Series
from src.foundation.backtest.vector.fills import VectorFillsError, VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import bool_from_series
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.e2e import test_e2e_4_script_to_backtest_report as base
from tests.support.perf_budget import PerfBudget, PerfSample
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_PIPELINE_BUDGET_MS = 1000.0


def _run_pipeline(source: str = base.SCRIPT_SOURCE) -> Decimal:
    compiled = compile_source(source, registry_version=base.REGISTRY_VERSION)
    result = execute(
        compiled.ir,
        bar_count=len(base._CLOSES),
        inputs={"close": Series.of_floats(float(c) for c in base._CLOSES)},
    )
    long_sig = bool_from_series(result.signals["go_long"])
    flat_sig = bool_from_series(result.signals["go_flat"])
    bt = run_vector_backtest(
        base._zero_cost_config(),
        base._candle_columns(),
        VectorSignal(entries=long_sig, exits=flat_sig, quantity=Decimal("1")),
        timeframe=Timeframe.M1,
        initial_cash=Decimal("100000"),
    )
    return bt.final_equity


# ---- 음성 테스트 ----------------------------------------------------------


def test_negative_type_error_script_rejected_with_script_type_code() -> None:
    """bool 신호 자리에 float 산술식 -- 타입체커(DSL-4)가 SCRIPT_TYPE으로 거부."""
    source = "input close: series<float> = 0\nsignal go_long = close + 1\n"
    with pytest.raises(ScriptCompileError) as exc_info:
        compile_source(source, registry_version=base.REGISTRY_VERSION)
    assert exc_info.value.code == "SCRIPT_TYPE"


def test_negative_lookahead_script_rejected_with_script_lookahead_code() -> None:
    """ta.security 미래 참조 -- lookahead 분석(DSL-5)이 SCRIPT_LOOKAHEAD로 거부."""
    source = "input c: series<float> = 0\nlet a = ta.security(c, 1)"
    with pytest.raises(ScriptCompileError) as exc_info:
        compile_source(source, registry_version=base.REGISTRY_VERSION)
    assert exc_info.value.code == "SCRIPT_LOOKAHEAD"


@pytest.mark.parametrize("bad_quantity", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_negative_non_positive_quantity_rejected_by_vector_signal(bad_quantity: Decimal) -> None:
    """수량 0/음수/NaN은 VectorSignal이 거부 -- 체결 엔진까지 도달하지 않는다."""
    compiled = compile_source(base.SCRIPT_SOURCE, registry_version=base.REGISTRY_VERSION)
    result = execute(
        compiled.ir,
        bar_count=len(base._CLOSES),
        inputs={"close": Series.of_floats(float(c) for c in base._CLOSES)},
    )
    sig = bool_from_series(result.signals["go_long"])
    with pytest.raises(VectorFillsError):
        VectorSignal(entries=sig, exits=sig, quantity=bad_quantity)


def test_negative_entries_exits_length_mismatch_rejected() -> None:
    """entries/exits 길이 불일치는 VectorSignal이 거부(I-05 입력 계약)."""
    compiled = compile_source(base.SCRIPT_SOURCE, registry_version=base.REGISTRY_VERSION)
    full = execute(
        compiled.ir,
        bar_count=len(base._CLOSES),
        inputs={"close": Series.of_floats(float(c) for c in base._CLOSES)},
    )
    short = execute(
        compiled.ir,
        bar_count=3,
        inputs={"close": Series.of_floats(float(c) for c in base._CLOSES[:3])},
    )
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=bool_from_series(full.signals["go_long"]),
            exits=bool_from_series(short.signals["go_flat"]),
            quantity=Decimal("1"),
        )


# ---- 실패 주입 ------------------------------------------------------------


def test_failure_injection_compile_error_aborts_before_backtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 -- 컴파일 거부 시 인터프리터/체결 엔진이 한 번도 호출되지 않는다."""
    calls: list[str] = []
    monkeypatch.setattr(base, "execute", lambda *a, **k: calls.append("execute"), raising=False)
    import tests.e2e.test_e2e_4_script_to_backtest_report_depth1 as me

    monkeypatch.setattr(me, "execute", lambda *a, **k: calls.append("execute"))
    monkeypatch.setattr(me, "run_vector_backtest", lambda *a, **k: calls.append("bt"))
    with pytest.raises(ScriptCompileError):
        _run_pipeline("input close: series<float> = 0\nsignal go_long = \n")
    assert calls == []


# ---- 게이트 적색 재현 -----------------------------------------------------


def _assert_final_equity(final_equity: Decimal) -> None:
    assert final_equity == Decimal("100003"), f"final_equity drifted: {final_equity}"


def test_gate_red_final_equity_assertion_catches_broken_input() -> None:
    """정상 입력은 녹색, 입력(스크립트의 신호 방향)을 망가뜨리면 같은 단언이 적색."""
    _assert_final_equity(_run_pipeline())  # 녹색
    inverted = (
        "input close: series<float> = 0\n"
        "signal go_long = close > close[1]\n"
        "signal go_flat = close < 0\n"
    )
    with pytest.raises(AssertionError, match="drifted"):
        _assert_final_equity(_run_pipeline(inverted))


# ---- 성능 단언 + 가상 지연 10배 적색 재현 ---------------------------------


def _assert_pipeline_budget(sample: PerfSample) -> None:
    assert sample.wall_ms < _PIPELINE_BUDGET_MS, f"E2E-4 pipeline budget: {sample.wall_ms:.3f}ms"


@pytest.mark.perf
def test_perf_pipeline_best_of_and_gate_red_10x_delay(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """컴파일+실행+벡터 백테스트 1회 best-of-5 wall < 예산. 컴파일에 예산 10배 가상
    지연을 주입하면 같은 단언이 적색이어야 한다."""
    reset_virtual_totals()
    healthy = perf_budget.best_of(_run_pipeline, n=5, warmup=1)
    print(f"E2E-4 baseline cpu={healthy.cpu_ms:.3f}ms wall={healthy.wall_ms:.3f}ms")
    _assert_pipeline_budget(healthy)

    import tests.e2e.test_e2e_4_script_to_backtest_report_depth1 as me

    monkeypatch.setattr(
        me, "compile_source", virtual_delayed_sync(compile_source, _PIPELINE_BUDGET_MS * 10 / 1000)
    )
    degraded = perf_budget.best_of(_run_pipeline, n=5)
    assert degraded.wall_ms >= _PIPELINE_BUDGET_MS * 10
    with pytest.raises(AssertionError, match="E2E-4 pipeline budget"):
        _assert_pipeline_budget(degraded)
    reset_virtual_totals()
