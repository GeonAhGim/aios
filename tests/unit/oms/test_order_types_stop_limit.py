"""스톱리밋 주문 단위테스트 — EM-19. DB 없음."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide, OrderType
from src.services.oms.domain.order_types.stop_limit import (
    InvalidStopLimitPriceError,
    is_stop_limit_triggered,
    resolve_triggered_order,
    validate_stop_limit_prices,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync


def test_buy_stop_limit_accepts_limit_at_or_above_trigger() -> None:
    validate_stop_limit_prices(
        side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("100.5")
    )
    validate_stop_limit_prices(
        side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("100")
    )


def test_buy_stop_limit_rejects_limit_below_trigger() -> None:
    with pytest.raises(InvalidStopLimitPriceError):
        validate_stop_limit_prices(
            side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("99.99")
        )


def test_sell_stop_limit_accepts_limit_at_or_below_trigger() -> None:
    validate_stop_limit_prices(
        side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("99.5")
    )
    validate_stop_limit_prices(
        side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("100")
    )


def test_sell_stop_limit_rejects_limit_above_trigger() -> None:
    with pytest.raises(InvalidStopLimitPriceError):
        validate_stop_limit_prices(
            side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("100.01")
        )


def test_stop_limit_trigger_condition_matches_stop() -> None:
    triggered = is_stop_limit_triggered(
        side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("100")
    )
    assert triggered is True
    not_triggered = is_stop_limit_triggered(
        side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("100.01")
    )
    assert not_triggered is False


def test_resolve_triggered_order_becomes_limit_at_limit_price() -> None:
    spec = resolve_triggered_order(limit_price=Decimal("99.5"))
    assert spec.order_type is OrderType.LIMIT
    assert spec.price == Decimal("99.5")


# ---------------------------------------------------------------------------
# 성능 테스트 — @pytest.mark.perf (D2 perf assertion 보강, task-24417)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_stop_limit_functions_cpu_budget() -> None:
    """validate_stop_limit_prices + is_stop_limit_triggered + resolve_triggered_order
    합산 CPU 예산 테스트.

    측정 방식: PerfBudget.best_of(n=10, batch=10)
    측정일: 2026-11-13
    실측 cpu_ms: 0.062ms (batch=10, Windows process_time 15.6ms 양자화)
    예산: 실측 × 30 = 1.86ms (여유 30배, ADR-2026-09-09-C Decision 1)
    """
    perf = PerfBudget()
    budget_ms = 1.86  # 0.062ms × 30

    def _work() -> None:
        validate_stop_limit_prices(
            side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("100.5")
        )
        validate_stop_limit_prices(
            side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("99.5")
        )
        is_stop_limit_triggered(
            side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("99")
        )
        resolve_triggered_order(limit_price=Decimal("99.5"))

    sample = perf.assert_within(
        _work,
        budget_ms=budget_ms,
        n=10,
        batch=10,
        label="stop_limit_functions",
    )
    margin_pct = (1 - sample.cpu_ms / budget_ms) * 100
    assert margin_pct > 0, (
        f"예산 여유 부족: {perf.describe(sample, budget_ms=budget_ms)} (여유 {margin_pct:.1f}%)"
    )


@pytest.mark.perf
def test_stop_limit_gate_red_via_virtual_delay() -> None:
    """게이트 적색 재현: virtual_delayed_sync 로 주입한 cpu_ns가
    PerfBudget.sample 에서 측정된 cpu_ms 를 예산 초과로 만든다.

    핵심: 예산은 실측의 30배이므로, 실측의 10배 주입으로는 예산을 넘지 못한다.
    따라서 주입량은 예산(실측×30)을 넘는 값으로 잡아야 적색 재현이 된다.
    여기서는 예산 × 1.5 의 가상 지연을 주입해 확실한 적색을 만든다.
    """
    perf = PerfBudget()
    budget_ms = 1.86  # 실측 0.062ms × 30

    def _work() -> None:
        validate_stop_limit_prices(
            side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("100.5")
        )
        validate_stop_limit_prices(
            side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("99.5")
        )
        is_stop_limit_triggered(
            side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("99")
        )
        resolve_triggered_order(limit_price=Decimal("99.5"))

    # 실제 함수를 가상 지연 함수로 감싸서 cpu 누계에 기여하게 한다.
    # 예산 × 1.5 (2.79ms) 주입 → 예산 초과 → 적색 재현.
    injection_seconds = budget_ms * 1.5 / 1000  # 0.00279초

    reset_virtual_totals()
    delayed_fn = virtual_delayed_sync(_work, injection_seconds)
    sample = perf.sample(delayed_fn, batch=1)

    # 주입이 없으면 예산 아래여야 함(양성 기준선)
    reset_virtual_totals()
    clean_sample = perf.sample(_work, batch=1)
    assert clean_sample.cpu_ms < budget_ms, (
        f"clean baseline이 이미 예산 초과: {perf.describe(clean_sample, budget_ms=budget_ms)}"
    )

    # 주입이 있으면 예산 초과 → 적색 재현
    assert sample.cpu_ms >= budget_ms, (
        f"gate-red 실패: 지연 주입에도 예산 미초과 {perf.describe(sample, budget_ms=budget_ms)}"
    )
