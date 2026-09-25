"""LB-3 — Weighted average cost method.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3
(`domain/cost_basis/weighted.py`: "weighted average: recompute average on buy,
hold average on sell"), `unit/positions/test_weighted.py` DoD("recompute average
on buy, invariant average on sell").

`FillEvent`/`CostBasisResult`/`NegativeQuantityError` are reused from [[fifo]] as-is —
FIFO/WEIGHTED are different lot-management strategies sharing the same I/O
representation (no duplicate definitions). `Lot` uses the LB-1 contract
(`contracts/v1.py`) unchanged.

Weighted average merges the entire position into a single blended average instead of
stacking lots — `lots` is always 0 (no position) or 1 (single blended lot). On buy,
average = (prev_qty × prev_avg + fill_qty × fill_price) / (prev_qty + fill_qty),
quantized to §3.4 precision (`NUMERIC(30,10)`, `Decimal("1e-10")`, `ROUND_HALF_EVEN`).
On sell, the average is unchanged; realized PnL = (fill_price − avg) × qty is computed
only — over-sell raises `NegativeQuantityError`, same as FIFO. Pure domain (zero I/O
imports) — time formatting, currency conversion, and persistence are the caller's
responsibility.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import Lot
from src.foundation.positions.domain.cost_basis.fifo import (
    CostBasisResult,
    FillEvent,
    NegativeQuantityError,
)

__all__ = ["WeightedAverage", "FillEvent", "CostBasisResult", "NegativeQuantityError"]

_PRICE_QUANTUM = Decimal("1e-10")


class WeightedAverage:
    """Weighted average cost method. Recomputes the average on every buy; on sell,
    keeps the average unchanged and extracts only realized PnL."""

    def __init__(self, lot: Lot | None = None) -> None:
        self._lot = lot

    @property
    def lots(self) -> tuple[Lot, ...]:
        return () if self._lot is None else (self._lot,)

    def apply(self, fill: FillEvent) -> CostBasisResult:
        if fill.side is OrderSide.BUY:
            return self._apply_buy(fill)
        return self._apply_sell(fill)

    def _apply_buy(self, fill: FillEvent) -> CostBasisResult:
        if self._lot is None:
            new_quantity = fill.quantity
            new_unit_cost = fill.price.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
            opened_at = fill.occurred_at
        else:
            new_quantity = self._lot.quantity + fill.quantity
            blended = (
                self._lot.quantity * self._lot.unit_cost + fill.quantity * fill.price
            ) / new_quantity
            new_unit_cost = blended.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
            opened_at = self._lot.opened_at
        self._lot = Lot(quantity=new_quantity, unit_cost=new_unit_cost, opened_at=opened_at)
        return CostBasisResult(realized_pnl=Decimal("0"), lots=self.lots)

    def _apply_sell(self, fill: FillEvent) -> CostBasisResult:
        available = Decimal("0") if self._lot is None else self._lot.quantity
        if fill.quantity > available:
            raise NegativeQuantityError(
                f"매도 수량({fill.quantity})이 보유 수량({available})을 초과합니다."
            )
        assert self._lot is not None  # if available > 0, _lot must exist

        realized = (fill.price - self._lot.unit_cost) * fill.quantity
        remaining_quantity = self._lot.quantity - fill.quantity
        if remaining_quantity == 0:
            self._lot = None
        else:
            self._lot = self._lot.model_copy(update={"quantity": remaining_quantity})
        return CostBasisResult(realized_pnl=realized, lots=self.lots)
