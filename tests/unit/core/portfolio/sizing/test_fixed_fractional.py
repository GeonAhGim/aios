"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 90, §8 line 572 — fixed_fractional.py tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingInputInvalidError
from src.core.portfolio.sizing.fixed_fractional import size
from src.core.portfolio.state_input import PortfolioStateInput
from tests.support.perf_inject import virtual_delayed_sync

_HEX64 = "a" * 64


def _config(**overrides):
    base = {
        "method": SizingMethod.FIXED_FRACTIONAL,
        "fraction_pct": Decimal("20"),
        "min_trade_notional": Decimal("10"),
        "cost_model": CostModelRef(model_id="cm-1", cost_model_hash=_HEX64),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def _inp(**overrides):
    base = {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9000"),
        "portfolio_config": _config(),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


# ---------------------------------------------------------------------------
# Positive / determinism
# ---------------------------------------------------------------------------


def test_size_matches_exact_decimal_formula():
    result = size(_inp())

    assert result.quantity == Decimal("0.004")  # 1000*20/100/50000
    assert result.weight_pct == Decimal("2")  # 200/10000*100
    assert result.method == SizingMethod.FIXED_FRACTIONAL
    assert len(result.inputs_hash) == 64


def test_size_is_deterministic_for_equal_inputs():
    assert size(_inp()).inputs_hash == size(_inp()).inputs_hash


def test_size_changes_hash_when_fraction_pct_changes():
    a = size(_inp())
    b = size(_inp(portfolio_config=_config(fraction_pct=Decimal("30"))))
    assert a.inputs_hash != b.inputs_hash


# ---------------------------------------------------------------------------
# Negative tests — invariant violations (I-01: fail-closed on invalid inputs)
# ---------------------------------------------------------------------------


def test_size_rejects_zero_price():
    with pytest.raises(SizingInputInvalidError):
        size(_inp(current_price=Decimal("0")))


def test_size_rejects_negative_price():
    with pytest.raises(SizingInputInvalidError):
        size(_inp(current_price=Decimal("-1")))


def test_size_rejects_zero_total_equity():
    """total_equity=0 → division-by-zero in weight_pct; must be rejected."""
    with pytest.raises(SizingInputInvalidError):
        size(_inp(total_equity=Decimal("0")))


def test_size_rejects_negative_total_equity():
    """Negative total_equity is an invariant violation; must be rejected."""
    with pytest.raises(SizingInputInvalidError):
        size(_inp(total_equity=Decimal("-500")))


# ---------------------------------------------------------------------------
# Failure injection — dependency exception (I-10: 배선 증명 테스트)
# ---------------------------------------------------------------------------


def test_size_propagates_require_positive_exception():
    """Monkeypatch require_positive to raise → size must re-raise unchanged."""
    with patch(
        "src.core.portfolio.sizing.fixed_fractional.require_positive",
        side_effect=RuntimeError("mock dependency failure"),
    ):
        with pytest.raises(RuntimeError, match="mock dependency failure"):
            size(_inp())


# ---------------------------------------------------------------------------
# Performance assertion — ADR-2026-09-09-C Decision 1 budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_size_completes_within_perf_budget(perf_budget):
    """size() must finish < 0.1 ms per call (budget: 100 ops/ms).

    raw perf_counter() → perf_budget.assert_within(batch=100) 전환(task-11672).
    process_time의 15.6ms 틱으로 빠른 1회 호출은 0/15.6만 나오므로 batch=100으로 묶어
    호출당 오차를 tick/100 ≈ 0.16ms로 낮췄다. 단위는 samples.cpu_ms가 ms이므로
    예산도 ms(100ms/1000회 = 0.1ms). 주입 지연 500ms를 주면 적색 게이트가 확인됨.
    best_of(n=5)로 Windows 양자화 tick에 흔들리지 않게 최소값을 선택한다."""
    perf_budget.assert_within(
        lambda: size(_inp()),
        budget_ms=0.1,
        n=5,
        warmup=1,
        batch=100,
        label="1000 iterations sizing",
    )


# ---------------------------------------------------------------------------
# Failure injection — perf budget violation via virtual delay (D2/D3)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_size_budget_fails_with_virtual_delay_injection(perf_budget):
    """virtual_delayed_sync으로 예산의 10배 지연을 주입하면 assert_within가 적색이 된다.

    실제 sleep/busy-wait 없이 virtual ledger에 지연만 추가하므로 테스트는 즉시 끝난다.
    PerfBudget가 ledger를 측정치에 합산하므로 같은 검출력을 가진다.
    """
    # 정상 입력은 녹색 — 예산 내
    sample_normal = perf_budget.sample(lambda: size(_inp()))
    assert sample_normal.cpu_ms < 0.1  # 예산 0.1ms

    # 예산 10배(1ms) 가상 지연 주입 → 적색
    slow_size = virtual_delayed_sync(lambda: size(_inp()), 0.001)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            slow_size,
            budget_ms=0.1,
            n=5,
            warmup=1,
            batch=100,
            label="virtual_delay 10× sizing",
        )


@pytest.mark.perf
def test_size_gate_red_reproduction_via_virtual_delay(perf_budget):
    """gate-red 재현(ADR-2026-09-09-C D2): virtual_delayed_sync로 CPU 부하를 주입해
    게이트가 적색으로 전환됨을 확인한다. 실제 busy-wait 없이 측정치만 늘린다."""
    # 50ms 가상 CPU 지연 — 예산(0.1ms)의 500배
    slow_size = virtual_delayed_sync(lambda: size(_inp()), 0.05)
    sample = perf_budget.sample(slow_size)
    assert sample.cpu_ms >= 50.0  # 가상 지연이 측정치에 반영됨
    # 예산을 훨씬 초과하므로 assert_within가 적색이어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            slow_size,
            budget_ms=0.1,
            n=5,
            warmup=1,
            batch=100,
            label="gate-red 50ms sizing",
        )
