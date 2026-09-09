"""CM-14: execution evidence using EM-12 prices and persisted EM-6 reasons.

Callers resolve the arrival snapshot and chronological benchmark window before
calling this pure function. Persistence and report submission belong to CM-16.
Positive slippage is an execution cost; negative slippage is price improvement.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from uuid import UUID

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.ems.domain.tca import benchmarks
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord


class BestExecutionInputMissing(ValueError):
    """Required evidence is absent; never substitute zero-cost evidence."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BenchmarkKind(str, Enum):
    ARRIVAL = "arrival"
    VWAP = "vwap"
    CLOSE = "close"


@dataclass(frozen=True)
class BestExecutionEvidence:
    """Immutable per-fill evidence, citing the original routing decision."""

    order_id: UUID
    route_decision_id: UUID
    venue: str
    reason_codes: tuple[str, ...]
    side: OrderSide
    execution_price: Decimal
    qty: Decimal
    benchmark: BenchmarkKind
    benchmark_price: Decimal
    slippage_bps: Decimal


def _positive_decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def best_execution(
    *,
    order_id: UUID,
    side: OrderSide,
    execution_price: Decimal,
    qty: Decimal,
    benchmark: BenchmarkKind,
    route_decision: RouteDecisionRecord | None,
    arrival_price: Decimal | None = None,
    benchmark_fills: Sequence[benchmarks.Fill] = (),
    bars: Sequence[Candle] = (),
) -> BestExecutionEvidence:
    """Compare one fill with an EM-12 benchmark without re-ranking venues.

    VWAP consumes EM-12 Fill samples; close consumes chronological candles.
    The supplied route row must belong to this order. Unknown reason codes
    are preserved verbatim, since their interpretation belongs to EMS.
    """
    if route_decision is None or not route_decision.decision.reason_codes:
        raise BestExecutionInputMissing("ROUTE_DECISION_MISSING")
    decision = route_decision.decision
    if any(not reason.strip() for reason in decision.reason_codes):
        raise BestExecutionInputMissing("ROUTE_DECISION_MISSING")
    if route_decision.order_id != order_id:
        raise ValueError("route decision belongs to another order")
    if not decision.venue.strip():
        raise BestExecutionInputMissing("ROUTE_DECISION_MISSING")
    if not isinstance(side, OrderSide):
        raise ValueError("side must be an OrderSide")
    _positive_decimal(execution_price, "execution_price")
    _positive_decimal(qty, "qty")

    if benchmark == BenchmarkKind.ARRIVAL:
        if arrival_price is None:
            raise BestExecutionInputMissing("ARRIVAL_PRICE_MISSING")
        _positive_decimal(arrival_price, "arrival_price")
        price = benchmarks.arrival_price(arrival_price)
    elif benchmark == BenchmarkKind.VWAP:
        if not benchmark_fills:
            raise BestExecutionInputMissing("BENCHMARK_FILLS_MISSING")
        for fill in benchmark_fills:
            _positive_decimal(fill.price, "benchmark fill price")
            _positive_decimal(fill.qty, "benchmark fill qty")
        price = benchmarks.compute_vwap(benchmark_fills)
    elif benchmark == BenchmarkKind.CLOSE:
        if not bars:
            raise BestExecutionInputMissing("BENCHMARK_BARS_MISSING")
        price = benchmarks.close_price(bars)
    else:
        raise ValueError("unsupported benchmark")
    _positive_decimal(price, "benchmark_price")
    direction = Decimal("1") if side == OrderSide.BUY else Decimal("-1")
    slippage = direction * (execution_price - price) / price * Decimal("10000")
    return BestExecutionEvidence(
        order_id=order_id,
        route_decision_id=route_decision.decision_id,
        venue=decision.venue,
        reason_codes=tuple(decision.reason_codes),
        side=side,
        execution_price=execution_price,
        qty=qty,
        benchmark=BenchmarkKind(benchmark),
        benchmark_price=price,
        slippage_bps=slippage,
    )
