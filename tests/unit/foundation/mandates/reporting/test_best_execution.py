"""CM-14 exact evidence, dependency delegation and fail-closed tests."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.domain.tca import benchmarks
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord
from src.foundation.mandates.reporting.domain.best_execution import (
    BenchmarkKind,
    BestExecutionInputMissing,
    best_execution,
)


@pytest.fixture
def inputs() -> dict[str, Any]:
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    order_id = uuid4()
    route = RouteDecisionRecord(
        decision_id=uuid4(), order_id=order_id,
        decision=RouteDecision(venue="test", reason_codes=["ONLY_VENUE", "CUSTOM_REASON"],
                               expected_cost_bps=Decimal("999")),
        candidates_snapshot=[], score_snapshot=[], weights_snapshot={},
        decided_at=now, created_at=now,
    )
    bar = Candle(symbol="BTC-USD", exchange="test", timeframe="1m",
                 open=Decimal("100"), high=Decimal("100"), low=Decimal("100"),
                 close=Decimal("100"), volume=Decimal("1"), open_time=now, close_time=now)
    return dict(order_id=order_id, side=OrderSide.BUY, execution_price=Decimal("100.5"),
                qty=Decimal("10"), benchmark=BenchmarkKind.ARRIVAL, route_decision=route,
                arrival_price=Decimal("100.0"), bars=[bar],
                benchmark_fills=[benchmarks.Fill(Decimal("100"), Decimal("10"))])


@pytest.mark.parametrize("side,expected", [(OrderSide.BUY, "50"), (OrderSide.SELL, "-50")])
def test_exact_slippage(inputs: dict[str, Any], side: OrderSide, expected: str) -> None:
    inputs["side"] = side
    result = best_execution(**inputs)
    assert result.slippage_bps == Decimal(expected)
    assert isinstance(result.slippage_bps, Decimal)
    assert result.qty == Decimal("10")
    assert result.route_decision_id == inputs["route_decision"].decision_id
    assert result.reason_codes == ("ONLY_VENUE", "CUSTOM_REASON")
    assert result == best_execution(**inputs)
    inputs["route_decision"].decision.reason_codes.append("LATER_MUTATION")
    assert result.reason_codes == ("ONLY_VENUE", "CUSTOM_REASON")


@pytest.mark.parametrize("kind,function", [
    (BenchmarkKind.ARRIVAL, "arrival_price"), (BenchmarkKind.VWAP, "compute_vwap"),
    (BenchmarkKind.CLOSE, "close_price"),
])
def test_delegation_failure_propagates(
    inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kind: BenchmarkKind, function: str,
) -> None:
    def fail(*args: object) -> Decimal:
        raise RuntimeError("EM-12 called")
    inputs["benchmark"] = kind
    monkeypatch.setattr(benchmarks, function, fail)
    with pytest.raises(RuntimeError, match="EM-12 called"):
        best_execution(**inputs)


@pytest.mark.parametrize("kind,field,value,code", [
    (BenchmarkKind.ARRIVAL, "arrival_price", None, "ARRIVAL_PRICE_MISSING"),
    (BenchmarkKind.VWAP, "benchmark_fills", [], "BENCHMARK_FILLS_MISSING"),
    (BenchmarkKind.CLOSE, "bars", [], "BENCHMARK_BARS_MISSING"),
])
def test_missing_benchmark(inputs: dict[str, Any], kind: BenchmarkKind,
                           field: str, value: object, code: str) -> None:
    inputs.update(benchmark=kind)
    inputs[field] = value
    with pytest.raises(BestExecutionInputMissing) as error:
        best_execution(**inputs)
    assert error.value.code == code


@pytest.mark.parametrize("reasons", [None, [], [""], [" "]])
def test_missing_route(inputs: dict[str, Any], reasons: list[str] | None) -> None:
    if reasons is None:
        inputs["route_decision"] = None
    else:
        inputs["route_decision"].decision.reason_codes = reasons
    with pytest.raises(BestExecutionInputMissing, match="ROUTE_DECISION_MISSING"):
        best_execution(**inputs)


def test_wrong_order(inputs: dict[str, Any]) -> None:
    inputs["route_decision"] = replace(inputs["route_decision"], order_id=uuid4())
    with pytest.raises(ValueError, match="another order"):
        best_execution(**inputs)


@pytest.mark.parametrize("field", ["execution_price", "qty", "arrival_price"])
@pytest.mark.parametrize("value", [100.5, Decimal("0"), Decimal("-1"),
                                  Decimal("NaN"), Decimal("Infinity")])
def test_invalid_numbers(inputs: dict[str, Any], field: str, value: object) -> None:
    inputs[field] = value
    with pytest.raises(ValueError, match="finite positive Decimal"):
        best_execution(**inputs)


@pytest.mark.parametrize("kind", [BenchmarkKind.VWAP, BenchmarkKind.CLOSE])
def test_other_benchmarks(inputs: dict[str, Any], kind: BenchmarkKind) -> None:
    inputs["benchmark"] = kind
    assert best_execution(**inputs).slippage_bps == Decimal("50")
