"""EM-14 (task-5277) -- TCA compute/read API request/response schemas.

HTTP transport shape only -- the contract itself is `src/foundation/ems/
contracts/v1.py::TcaResult` (106 §2), wrapped rather than duplicated.
`side`/`fills`/`bars` mirror `application/compute_tca.py`'s parameters
one-to-one so the router does no translation beyond building `Fill`/
`Candle` domain objects out of the wire `Decimal`s.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import TcaResult

__all__ = [
    "AlgoProgressView",
    "ComputeTcaBar",
    "ComputeTcaFill",
    "ComputeTcaRequest",
    "TcaResultView",
]


class ComputeTcaFill(BaseModel):
    price: Decimal
    qty: Decimal


class ComputeTcaBar(BaseModel):
    close: Decimal
    volume: Decimal


class ComputeTcaRequest(BaseModel):
    side: OrderSide
    fills: list[ComputeTcaFill]
    price_at_arrival_ts: Decimal
    bars: list[ComputeTcaBar]
    spread_cost: Decimal
    fees: Decimal
    total_cost: Decimal
    revision: int = 1
    computed_at: datetime


class TcaResultView(BaseModel):
    parent_id: UUID
    revision: int
    result: TcaResult
    computed_at: datetime


class AlgoProgressView(BaseModel):
    parent_id: UUID
    status: str
    total_slices: int
    submitted_slices: int
    pending_slices: int
    remaining_qty: Decimal
    demoted_to_twap: bool
    demotion_reason: str | None
