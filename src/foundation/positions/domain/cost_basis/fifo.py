"""LB-2 — FIFO cost-basis lot queue.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2
(`domain/cost_basis/fifo.py`: "FIFO lot queue: buys push lots, sells
consume from head, realized PnL = Σ(settlement price − lot cost) × qty"),
`unit/positions/test_fifo.py` DoD("buy 10@100, 5@110, sell 12 → realize
(12: 10@100+2@110), remaining lot 3@110; over-sell → `POS_NEGATIVE_QUANTITY`;
JSON round-trip").

`Lot`(quantity, unit cost, open time) is reused from the LB-1 contract
(`contracts/v1.py`) because it is shared between FIFO and WEIGHTED (LB-3).
`FillEvent` / `CostBasisResult` are pure I/O objects consumed/produced by
this leaf and not defined in the contract — account currency (`Money`) and
portfolios do not enter this layer (conversion is LB-4 `fx.py` / `pnl.py`'s
responsibility).

`FifoLots` is mutable state holding a lot queue (buys push to the tail,
sells consume from the head as the name implies) — unlike other leaves in
this package (`balance_rules`, etc.) that prefer stateless pure functions,
continuously applying fills in journal-fold order is the reason this type
exists, so mutability is intentional. It performs no I/O — timestamps,
currency conversion, and persistence are the caller's responsibility.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import Lot


class NegativeQuantityError(Exception):
    """`POS_NEGATIVE_QUANTITY` — sell quantity exceeds total held lots.
    Spot short-selling is prohibited — not retryable, indicates an order-path bug."""


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime is not allowed — use tz-aware UTC only")
    return value


@dataclass(frozen=True, slots=True)
class FillEvent:
    """A single fill to apply to the FIFO queue. `price` is a raw `Decimal`,
    not account-currency `Money` — cost basis does not know about currencies."""

    side: OrderSide
    quantity: Decimal
    price: Decimal
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_aware_utc(self.occurred_at)
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive: {self.quantity}")


@dataclass(frozen=True, slots=True)
class CostBasisResult:
    """Result of a single `apply`. `realized_pnl` is the PnL realized by
    this fill only (not cumulative), and `lots` is a snapshot of the full queue after applying."""

    realized_pnl: Decimal
    lots: tuple[Lot, ...]


class FifoLots:
    """FIFO lot queue. Each buy pushes a `Lot` to the tail; sells consume
    from the head (oldest lot first)."""

    def __init__(self, lots: tuple[Lot, ...] = ()) -> None:
        self._lots: list[Lot] = list(lots)

    @property
    def lots(self) -> tuple[Lot, ...]:
        return tuple(self._lots)

    def apply(self, fill: FillEvent) -> CostBasisResult:
        if fill.side is OrderSide.BUY:
            return self._apply_buy(fill)
        return self._apply_sell(fill)

    def _apply_buy(self, fill: FillEvent) -> CostBasisResult:
        self._lots.append(
            Lot(quantity=fill.quantity, unit_cost=fill.price, opened_at=fill.occurred_at)
        )
        return CostBasisResult(realized_pnl=Decimal("0"), lots=self.lots)

    def _apply_sell(self, fill: FillEvent) -> CostBasisResult:
        available = sum((lot.quantity for lot in self._lots), Decimal("0"))
        if fill.quantity > available:
            raise NegativeQuantityError(
                f"sell quantity ({fill.quantity}) exceeds total held lots ({available})"
            )

        remaining = fill.quantity
        realized = Decimal("0")
        new_lots: list[Lot] = []
        for lot in self._lots:
            if remaining <= 0:
                new_lots.append(lot)
                continue
            consumed = min(lot.quantity, remaining)
            realized += (fill.price - lot.unit_cost) * consumed
            remaining -= consumed
            if consumed < lot.quantity:
                new_lots.append(lot.model_copy(update={"quantity": lot.quantity - consumed}))
        self._lots = new_lots
        return CostBasisResult(realized_pnl=realized, lots=self.lots)

    def to_json(self) -> str:
        return json.dumps([lot.model_dump(mode="json") for lot in self._lots])

    @classmethod
    def from_json(cls, data: str) -> FifoLots:
        raw = json.loads(data)
        return cls(tuple(Lot.model_validate(item) for item in raw))
