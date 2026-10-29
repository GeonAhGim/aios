"""LB-3 — WeightedAverage 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3,
`unit/positions/test_weighted.py` DoD("평단 재계산, 매도 시 평단 불변").
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import Lot
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from src.foundation.positions.domain.cost_basis.weighted import FillEvent, WeightedAverage
from tests.support.perf_budget import PerfBudget


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


def _fill(side: OrderSide, quantity: str, price: str) -> FillEvent:
    return FillEvent(
        side=side, quantity=Decimal(quantity), price=Decimal(price), occurred_at=_now()
    )


def test_first_buy_sets_avg_cost_to_fill_price() -> None:
    wavg = WeightedAverage()
    result = wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    assert result.realized_pnl == Decimal("0")
    assert len(result.lots) == 1
    assert result.lots[0].quantity == Decimal("10")
    assert result.lots[0].unit_cost == Decimal("100")


def test_second_buy_recomputes_weighted_average_cost() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))
    result = wavg.apply(_fill(OrderSide.BUY, "10", "110"))

    # (10*100 + 10*110) / 20 = 105
    assert result.lots[0].quantity == Decimal("20")
    assert result.lots[0].unit_cost == Decimal("105.0000000000")


def test_sell_keeps_avg_cost_unchanged_and_computes_realized_pnl() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))
    wavg.apply(_fill(OrderSide.BUY, "10", "110"))

    result = wavg.apply(_fill(OrderSide.SELL, "5", "120"))

    # 평단은 105로 유지, 실현손익 = (120-105)*5 = 75
    assert result.realized_pnl == Decimal("75")
    assert result.lots[0].quantity == Decimal("15")
    assert result.lots[0].unit_cost == Decimal("105.0000000000")


def test_sell_closing_full_position_leaves_no_lots() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    result = wavg.apply(_fill(OrderSide.SELL, "10", "120"))

    assert result.realized_pnl == Decimal("200")
    assert result.lots == ()


def test_buy_after_full_close_resets_avg_cost() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))
    wavg.apply(_fill(OrderSide.SELL, "10", "120"))

    result = wavg.apply(_fill(OrderSide.BUY, "5", "90"))

    assert result.lots[0].quantity == Decimal("5")
    assert result.lots[0].unit_cost == Decimal("90.0000000000")


def test_sell_exceeding_available_quantity_rejected() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    with pytest.raises(NegativeQuantityError):
        wavg.apply(_fill(OrderSide.SELL, "11", "120"))

    # 거부된 매도는 상태를 바꾸지 않는다.
    assert wavg.lots[0].quantity == Decimal("10")


def test_sell_with_no_position_rejected() -> None:
    wavg = WeightedAverage()
    with pytest.raises(NegativeQuantityError):
        wavg.apply(_fill(OrderSide.SELL, "1", "100"))


def test_zero_quantity_fill_rejected() -> None:
    """불변식: quantity > 0. Zero 수량은 FillEvent 생성 단계에서 거부된다."""
    with pytest.raises(ValueError) as exc_info:
        FillEvent(
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            price=Decimal("100"),
            occurred_at=_now(),
        )
    # Language-agnostic: check exception type + that the message mentions quantity
    assert "quantity" in str(exc_info.value)


def test_apply_lot_model_copy_failure_propagates() -> None:
    """실패주입: Lot.model_copy 가 예외를 던지면 apply 도 그대로 전파한다."""
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    lot_cls = type(cast(Lot, wavg._lot))
    original_model_copy = lot_cls.model_copy

    def failing_model_copy(self: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated persistence failure")

    setattr(lot_cls, "model_copy", failing_model_copy)  # noqa: B010

    try:
        with pytest.raises(RuntimeError, match="simulated persistence failure"):
            wavg.apply(_fill(OrderSide.SELL, "5", "120"))
    finally:
        setattr(lot_cls, "model_copy", original_model_copy)  # noqa: B010


@pytest.mark.perf
def test_weighted_average_10k_fills_within_budget(perf_budget) -> None:
    """성능 단언: 10,000회 연속 매수+매도 사이클이 5초 이내야 한다.

    raw time.monotonic() → perf_budget.assert_within() 전환(task-11005).
    """

    def _run() -> None:
        wavg = WeightedAverage()
        n = 10_000
        for i in range(n):
            price = Decimal("100") + Decimal(str(i % 100))
            wavg.apply(_fill(OrderSide.BUY, "1", str(price)))
            wavg.apply(_fill(OrderSide.SELL, "1", str(price)))

    sample = perf_budget.assert_within(_run, budget_ms=5000.0, label="10k_fills")

    # D2: 10k 사이클 ≤ 5초 (≈2,000 ops/s)
    print(f"[weighted] 10k 사이클 {sample.cpu_ms:.0f}ms (budget<5000ms)")


# ---------- 단일 호출 CPU 수렴 (task-11539 감사 후속) ----------


def test_single_call_cpu_converges_to_zero(perf_budget: PerfBudget) -> None:
    """단일 apply() 호출이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로
    batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다. 1회당 CPU 시간이
    1ms 미만이어야 순수 도메인 로직이라는 불변식(I-03)과 호환된다.
    """
    wavg = WeightedAverage()

    def _one_call() -> None:
        wavg.apply(_fill(OrderSide.BUY, "1", "100"))

    sample = perf_budget.sample(_one_call, batch=64)

    # CPU 시간이 1ms 미만 (양자화 오차 포함)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"순수 도메인 로직은 1ms 미만이어야 함 (budget<1ms)"
    )


@pytest.mark.perf
def test_gate_red_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(1ms)을 100배 초과하는 CPU 시간을 가장하면
    같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 100ms를 반환하므로
    batch=64로 나눈 평균도 100ms > 1ms budget로 적색 확정.
    """
    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 100ms(100x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.1  # 100ms per call = 100x budget
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    wavg = WeightedAverage()

    def _one_call() -> None:
        wavg.apply(_fill(OrderSide.BUY, "1", "100"))

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_one_call, batch=64)
        assert sample.cpu_ms < 1.0, (
            f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"순수 도메인 로직은 1ms 미만이어야 함 (budget<1ms)"
        )
