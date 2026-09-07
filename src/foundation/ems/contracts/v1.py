"""EM-1 — EMS (execution: routing, algos, TCA) contracts v1.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 module table, §3
contract summary, §9 EM-1.

`AlgoSpec` is the canonical replacement for OMS
`src/services/oms/contracts/v1_commands.py`'s `AlgoRequest` (2026-09-06
audit). `ICEBERG` is absorbed as one `kind` value, and `AlgoRequest`'s
`size_jitter_pct`/`time_jitter_pct` are dropped here because they conflict
with EM-A3 (determinism: same snapshot + same spec -> same plan) — `seed`
alone drives a reproducible slice plan (same seed -> same plan). The
existing `AlgoRequest` is not deleted in this leaf (call-site migration is
EM-3's job).

Follows the `src/foundation/entities/contracts/v1.py` (FA-1) convention:
this module does not import `domain/`, and downstream domain layers
(EM-2+) import only this contract (standard 71 §4). Error codes are
defined here as values only; the actual exception classes belong to the
domain leaf that raises them (EM-2/EM-7 etc.), same principle as FA-1's
`EntityErrorCode`.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, BeforeValidator, Field, model_validator
from starlette import status

from src.data.models.trading import OrderSide, OrderStatus

SCHEMA_VERSION: Literal["v1"] = "v1"

TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
        OrderStatus.FAILED,
    }
)
"""EM-A4 — a parent in one of these states may not spawn new children
(`EM_PARENT_TERMINAL`). Reuses the shared-contract `OrderStatus` (standard
01) verbatim — EMS does not define its own status axis. `UNKNOWN` is not
terminal, per the 8.3 principle."""


def _reject_float(value: object) -> object:
    """Reject float input on money/quantity/bps fields (precision loss).

    pydantic v2 accepts float on `Decimal` fields by default (it does not
    round-trip through `str(value)`), which lets binary floating-point
    error leak in. This project only accepts `int`/`str`/`Decimal` input
    and rejects float by type (string is the only lossless numeric-text
    path).
    """
    if isinstance(value, float):
        raise ValueError("float is not accepted — pass a Decimal or a string instead.")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class EmsErrorCode(str, Enum):
    """§3 error taxonomy — must match the spec literally (snapshot test)."""

    ALGO_CONSTRAINT = "EM_ALGO_CONSTRAINT"  # 400 — AlgoSpec constraint violated (bad kind, etc.)
    NO_ROUTE = "EM_NO_ROUTE"  # 409 — venue outage, no fallback route available
    PARENT_TERMINAL = "EM_PARENT_TERMINAL"  # 409 — new child against a terminal parent
    PARTICIPATION_EXCEEDED = "EM_PARTICIPATION_EXCEEDED"  # 409 — participation cap exceeded


HTTP_STATUS: dict[EmsErrorCode, int] = {
    EmsErrorCode.ALGO_CONSTRAINT: status.HTTP_400_BAD_REQUEST,
    EmsErrorCode.NO_ROUTE: status.HTTP_409_CONFLICT,
    EmsErrorCode.PARENT_TERMINAL: status.HTTP_409_CONFLICT,
    EmsErrorCode.PARTICIPATION_EXCEEDED: status.HTTP_409_CONFLICT,
}


class AlgoKind(str, Enum):
    """§3 `AlgoSpec.kind` — values outside this table (e.g. `sniper`) are
    rejected by pydantic."""

    TWAP = "twap"
    VWAP = "vwap"
    POV = "pov"
    IS = "is"
    ICEBERG = "iceberg"


class AlgoSpec(BaseModel):
    """§3 — canonical replacement for OMS `AlgoRequest`. `seed` alone
    reproduces a deterministic slice plan (EM-A3) — no jitter-ratio fields
    are carried here."""

    kind: AlgoKind
    start: AwareDatetime
    end: AwareDatetime
    max_participation_pct: StrictDecimal = Field(gt=0, le=100)
    slice_interval_sec: int = Field(gt=0)
    urgency: StrictDecimal = Field(ge=0, le=1)
    limit_price: StrictDecimal | None = Field(default=None, gt=0)
    seed: int
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_window(self) -> AlgoSpec:
        if self.end <= self.start:
            raise ValueError("end must be after start.")
        return self


class ParentOrderConstraints(BaseModel):
    """EM-A2 — caps that no algorithm may loosen. Carries the value
    produced by the risk/compliance gate that already approved the
    parent; it is not recomputed here (recomputation is each gate's own
    responsibility, I-09). The exact field set is settled by the leaves
    that actually consume it — EM-6 (routing) and EM-7 (algo guard) — this
    contract only fixes the one cap both already agree on
    (participation)."""

    max_participation_pct: StrictDecimal = Field(gt=0, le=100)
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ParentOrder(BaseModel):
    """§2 contract table — `ParentOrder{parent_id, instrument_id, side,
    qty, algo, constraints, fund_id, portfolio_id}`. `arrival_ts` is added
    beyond the table because §3's prose requires it ("the TCA benchmark
    reference time is fixed to the parent order's arrival_ts") — same
    rationale as FA-1 adding `closed_at`. `status` feeds the EM-A4
    (`EM_PARENT_TERMINAL`) check."""

    parent_id: UUID
    instrument_id: str
    side: OrderSide
    qty: StrictDecimal = Field(gt=0)
    algo: AlgoSpec
    constraints: ParentOrderConstraints
    fund_id: UUID
    portfolio_id: UUID
    arrival_ts: AwareDatetime
    status: OrderStatus = OrderStatus.CREATED
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ChildOrder(BaseModel):
    """§2 contract table — `ChildOrder{child_id, parent_id, slice_seq,
    ...}`. `order_id` is populated only after the slice has passed
    `submit_order` and a real order exists (§3: "child orders pass through
    submit_order with no exception") — `None` means the slice is still a
    plan that has not reached any adapter. Fill state and status live only
    on the shared-contract `Order` (standard 01, looked up by `order_id`)
    and are not duplicated here (SSOT)."""

    child_id: UUID
    parent_id: UUID
    slice_seq: int = Field(ge=0)
    instrument_id: str
    side: OrderSide
    planned_qty: StrictDecimal = Field(gt=0)
    scheduled_at: AwareDatetime
    limit_price: StrictDecimal | None = Field(default=None, gt=0)
    order_id: UUID | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class RouteDecision(BaseModel):
    """§3 — `RouteDecision{venue, reason_codes, expected_cost_bps}`.
    `reason_codes` are examples (`BEST_FEE`/`DEEPEST_BOOK`/`ONLY_VENUE`),
    not a closed set (§3 says "e.g.") — kept as free-form strings rather
    than a closed enum."""

    venue: str
    reason_codes: list[str] = Field(min_length=1)
    expected_cost_bps: StrictDecimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class TcaResult(BaseModel):
    """§3 — `TcaResult{arrival_bps, vwap_bps, impact_bps, fees_bps,
    opportunity_bps}`. The invariant that the decomposition sums to total
    cost (EM-A5/§8) is enforced by `domain/tca/decomposition.py` (EM-13) —
    this contract only defines the shape."""

    arrival_bps: StrictDecimal
    vwap_bps: StrictDecimal
    impact_bps: StrictDecimal
    fees_bps: StrictDecimal
    opportunity_bps: StrictDecimal
    schema_version: Literal["v1"] = SCHEMA_VERSION
