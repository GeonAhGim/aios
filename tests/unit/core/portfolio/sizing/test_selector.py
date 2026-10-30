"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 94, §8 line 572 — selector.py tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingResult, fixed_fractional
from src.core.portfolio.sizing.selector import (
    _DISPATCH,
    SizingResultTamperedError,
    UnknownSizingMethodError,
    size_for,
)
from src.core.portfolio.state_input import PortfolioAggregate, PortfolioStateInput
from tests.conftest import PerfBudget

_HEX64 = "e" * 64
_AS_OF = datetime(2026, 9, 9, tzinfo=UTC)


def _config(method: SizingMethod, **overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": method,
        "fraction_pct": Decimal("20"),
        "target_vol_pct": Decimal("10"),
        "kelly_cap_pct": Decimal("25"),
        "min_trade_notional": Decimal("10"),
        "cost_model": CostModelRef(model_id="cm-1", cost_model_hash=_HEX64),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def _inp(method: SizingMethod, **overrides: Any) -> PortfolioStateInput:
    base: dict[str, Any] = {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9000"),
        "realized_vol_pct": Decimal("20"),
        "win_rate": Decimal("0.6"),
        "avg_win_loss_ratio": Decimal("2"),
        "exposures": PortfolioAggregate(
            total_equity=Decimal("10000"),
            per_symbol_pct={},
            per_strategy_pct={},
            total_exposure_pct=Decimal("0"),
            cash_pct=Decimal("100"),
            as_of=_AS_OF,
        ),
        "portfolio_config": _config(method),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


@pytest.mark.parametrize("method", [m for m in SizingMethod], ids=[m.value for m in SizingMethod])
def test_size_for_dispatches_to_the_matching_module(method: SizingMethod):
    cfg = _config(method)
    inp = _inp(method)

    dispatched = size_for(cfg, inp)
    direct = _DISPATCH[method](inp)

    assert dispatched == direct
    assert dispatched.method == method


@pytest.mark.perf
def test_size_for_rejects_config_input_method_mismatch(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """불일치 3종을 거부하고 동일한 CPU 예산 단언의 검출력을 검증한다."""
    from src.core.portfolio.sizing import selector
    from tests.support.perf_inject import virtual_delayed_sync

    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    original = selector.size_for

    def reject() -> None:
        with pytest.raises(SizingResultTamperedError, match="config.method="):
            selector.size_for(cfg, inp)

    def assert_budget() -> float:
        # best_of removes isolated Windows ticks; batch preserves per-call ms.
        best = perf_budget.best_of(reject, n=10, warmup=2, batch=100)
        assert best.cpu_ms < 1.0, (
            f"mismatch rejection should be near-zero CPU; "
            f"measured best_of_min={best.cpu_ms:.3f}ms"
        )
        return best.cpu_ms

    for method in (
        SizingMethod.VOLATILITY_TARGET,
        SizingMethod.KELLY_CAPPED,
        SizingMethod.RISK_PARITY,
    ):
        inp = _inp(method)
        normal_ms = assert_budget()
        with monkeypatch.context() as patch:
            # Virtual CPU load: exactly 10 times the unchanged 1 ms budget.
            patch.setattr(selector, "size_for", virtual_delayed_sync(original, 0.010))
            with pytest.raises(AssertionError, match="mismatch rejection should") as red:
                assert_budget()
        assert_budget()  # Restoration must also restore the green result.
        print(f"{method.value}: normal={normal_ms:.6f}ms; gate-red={red.value}")


@pytest.mark.perf
def test_size_for_rejects_config_input_method_mismatch_goes_red_with_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction: `virtual_delayed_sync` 로 `size_for` 진입 전
    가상 지연을 주입하면 같은 p99 단언이 적색으로 전환된다.

    불일치 경로는 dispatch 전 비교이므로
    `virtual_delayed_sync(original_size_for, 0.05)` 로 전체 함수를 감싸
    가상 CPU 부하를 주입한다(실제 busy-wait 금지).
    """
    from tests.support.perf_inject import virtual_delayed_sync

    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    inp = _inp(SizingMethod.VOLATILITY_TARGET)
    budget_ms = 1.0

    original_size_for = size_for
    delayed_size_for = virtual_delayed_sync(original_size_for, 0.05)  # 50ms virtual delay
    monkeypatch.setattr("tests.unit.core.portfolio.sizing.test_selector.size_for", delayed_size_for)

    samples = perf_budget.samples(lambda: _safe_mismatch_call(cfg, inp), n=100, batch=10)
    cpu_ms_values = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.99)]

    assert p99_ms >= budget_ms, (
        f"delay injection failed: p99={p99_ms:.3f}ms — "
        f"expected red (budget>={budget_ms}ms); measurement is hollow"
    )


# ---------------------------------------------------------------------------
# Helper: call size_for for mismatch, swallow exception for measurement
# ---------------------------------------------------------------------------

_NOT_RAISED = object()


def _safe_mismatch_call(cfg: PortfolioConfig, inp: PortfolioStateInput) -> object:
    """Call ``size_for(cfg, inp)`` and return ``_NOT_RAISED`` if the
    expected ``SizingResultTamperedError`` is raised — so
    ``PerfBudget.sample`` can measure the CPU time without crashing.
    """
    try:
        size_for(cfg, inp)
    except SizingResultTamperedError:
        pass
    return _NOT_RAISED


def test_size_for_rejects_unknown_method(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, None)

    with pytest.raises(UnknownSizingMethodError):
        size_for(_config(SizingMethod.FIXED_FRACTIONAL), _inp(SizingMethod.FIXED_FRACTIONAL))


def test_size_for_rejects_malformed_inputs_hash(monkeypatch: pytest.MonkeyPatch):
    def _tampered(inp: PortfolioStateInput) -> SizingResult:
        result = fixed_fractional.size(inp)
        return result.model_copy(update={"inputs_hash": "not-a-hash"})

    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, _tampered)

    with pytest.raises(SizingResultTamperedError):
        size_for(_config(SizingMethod.FIXED_FRACTIONAL), _inp(SizingMethod.FIXED_FRACTIONAL))


def test_size_for_rejects_result_method_mismatch(monkeypatch: pytest.MonkeyPatch):
    def _wrong_method(inp: PortfolioStateInput) -> SizingResult:
        result = fixed_fractional.size(inp)
        return result.model_copy(update={"method": SizingMethod.KELLY_CAPPED})

    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, _wrong_method)

    with pytest.raises(SizingResultTamperedError):
        size_for(_config(SizingMethod.FIXED_FRACTIONAL), _inp(SizingMethod.FIXED_FRACTIONAL))


@pytest.mark.perf
def test_size_for_p99_latency_is_within_pre_trade_gate_budget(perf_budget: PerfBudget):
    """사이징은 주문 제출 전 경로에 있으므로 ADR-2026-09-09-C Decision 1의
    "사전거래 게이트 p99 5ms" 예산이 적용된다 — 4개 산식 전부를 디스패치해도
    한 번의 size_for() 호출이 그 예산 안에 들어야 한다."""
    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    inp = _inp(SizingMethod.FIXED_FRACTIONAL)
    budget_ms = 5.0

    samples = perf_budget.samples(lambda: size_for(cfg, inp), n=500, batch=50)
    cpu_ms_values = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.99)]

    assert p99_ms < budget_ms, (
        f"size_for() p99={p99_ms:.3f}ms exceeds {budget_ms}ms pre-trade gate budget"
    )


# ---------------------------------------------------------------------------
# Negative tests — CPU quantisation & measurement integrity (task-11539 후속)
# ---------------------------------------------------------------------------


def test_size_for_returns_consistent_results_for_identical_inputs(perf_budget: PerfBudget):
    """음성: 동일한 입력에 대한 반복 호출은 항상 동일한 결과를 반환한다
    (side-effect 가 없고 memoisation 이 없어야 함)."""
    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    inp = _inp(SizingMethod.FIXED_FRACTIONAL)

    results = [perf_budget.sample(lambda: size_for(cfg, inp)).result for _ in range(5)]
    assert all(r == results[0] for r in results), (
        "size_for produced inconsistent results for identical inputs"
    )


def test_size_for_dispatch_is_hollow_without_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
):
    """음성: _DISPATCH[_FIXED_FRACTIONAL] 을 None 으로 monkeypatch 하면
    size_for 가 UnknownSizingMethodError 를 raising 해야 한다 — 단언이
    hollow(실행 경로에 도달하지 않음) 아님을 확인."""
    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, None)

    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    inp = _inp(SizingMethod.FIXED_FRACTIONAL)

    with pytest.raises(UnknownSizingMethodError):
        size_for(cfg, inp)

    # perf_budget.sample 는 위 raise 전에 측정하지 않음 —
    # 이 테스트는 "단언이 실제 코드를 건넜음"을 증명하는 negative test


# ---------------------------------------------------------------------------
# Failure injection — gate-red reproduction with CPU delay
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_size_for_p99_budget_goes_red_with_cpu_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
):
    """Gate-red reproduction: `delayed_sync` 로 dispatch 함수에 CPU 부하를
    주입하면 같은 p99 단언이 적색으로 전환된다.

    `delayed_sync` 은 busy-wait(CPU-time slowdown) 를 주입한다.
    `PerfBudget.sample` 는 `time.process_time()` 을 사용하므로 CPU 지연이
    직접 포착된다. 이 테스트가 없으면 예산 단언은 항상 통과하는 hollow 가
    될 수 있다.
    """
    from tests.support.perf_inject import delayed_sync

    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    inp = _inp(SizingMethod.FIXED_FRACTIONAL)
    budget_ms = 5.0

    original_fn = _DISPATCH[SizingMethod.FIXED_FRACTIONAL]
    delayed_fn = delayed_sync(original_fn, 0.05)  # 50ms CPU delay
    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, delayed_fn)

    samples = perf_budget.samples(lambda: size_for(cfg, inp), n=100, batch=10)
    cpu_ms_values = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.99)]

    assert p99_ms >= budget_ms, (
        f"delay injection failed: p99={p99_ms:.3f}ms — "
        f"expected red (budget>={budget_ms}ms); measurement is hollow"
    )
