"""BT-1 — Backtest realism contract v2 (`BacktestConfigV2`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-1, §3.4 (backtest realism contract), §9.5 BT-1,
107_contract_versioning_and_compatibility_standard_v1.0.md §3.3 (MAJOR change).

`domain/models.py` (v1, `BacktestConfig`) is the contract that only supports
the fixed-bps slippage/commission from spec 109. This v2 adds a fill-realism
model (3 slippage kinds, commission tiers, latency, partial fills, order
types, bar magnifier, funding/borrow costs, adjustments, calendar), so it is
a new contract kept alongside v1 without modifying it (spec 107 §3.3 — a
change in field meaning goes into a new version module; existing modules
stay immutable).

BT-2~7 (fill models) and BT-9 (reproducibility key) depend 1:1 on this
contract, so fields outside the §3.4 table must not be added arbitrarily.
The `reproducibility_key` (reproducibility key, owned by BT-9) formula
itself is not implemented here — `BacktestConfigV2.canonical_json()` only
guarantees the canonical serialization that is the input to that hash
(determinism: the same-valued model always yields the same byte sequence).

All amounts/ratios/fees are `Decimal` (float is forbidden, to keep binary
floating-point error out of the fill-realism model's comparison and
accumulation calculations).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

from src.foundation.market_data.contracts.v1 import Timeframe

SCHEMA_VERSION: Literal["backtest-v2"] = "backtest-v2"


def _reject_float(value: object) -> object:
    """Reject `float` explicitly, since it carries binary floating-point
    error (module docstring's "no float" invariant — pydantic's default
    behavior silently coerces `float` into `Decimal`, so without this
    validator the invariant is never wired in). `int`/`str`/`Decimal`
    pass through unchanged."""

    if isinstance(value, float):
        raise ValueError("float is not accepted here — pass Decimal or a decimal string")
    return value


NonNegativeDecimal = Annotated[Decimal, BeforeValidator(_reject_float), Field(ge=0)]
UnitDecimal = Annotated[Decimal, BeforeValidator(_reject_float), Field(gt=0, le=1)]
OptionalNonNegativeDecimal = Annotated[Decimal, BeforeValidator(_reject_float), Field(ge=0)] | None


class FixedSlippage(BaseModel):
    """Assumes a fill price worse by a fixed bps on every bar."""

    kind: Literal["fixed"] = "fixed"
    bps: NonNegativeDecimal


class PercentSlippage(BaseModel):
    """Assumes a fill price worse by a fixed percentage of the fill price."""

    kind: Literal["percent"] = "percent"
    pct: NonNegativeDecimal


class VolumeImpactSlippage(BaseModel):
    """Applies market impact proportional to the order's participation rate
    in the bar's volume. `participation_cap` is the max participation rate
    a single bar can absorb (greater than 0, at most 1) — carrying over the
    excess is the fill model's (BT-2~7) responsibility."""

    kind: Literal["volume_impact"] = "volume_impact"
    k: NonNegativeDecimal
    participation_cap: UnitDecimal


SlippageModel = Annotated[
    FixedSlippage | PercentSlippage | VolumeImpactSlippage,
    Field(discriminator="kind"),
]


class VenueTierCommission(BaseModel):
    """Maker/taker commission per venue/tier + minimum fee (flat amount)."""

    venue: str
    maker_bps: NonNegativeDecimal
    taker_bps: NonNegativeDecimal
    min_fee: NonNegativeDecimal


class PartialFillConfig(BaseModel):
    """Max participation rate fillable in a single bar (greater than 0, at
    most 1) — orders exceeding it are handled as partial fills by the fill
    model (BT-5)."""

    max_participation_pct: UnitDecimal


class OrderTypesConfig(BaseModel):
    """Switches for order types the engine allows. An order of a disabled
    type is rejected by the fill model (BT-6)."""

    limit: bool
    stop: bool
    oco: bool
    trailing: bool


class CostsConfig(BaseModel):
    """`borrow_apr` only applies to borrowed positions such as short sales,
    so a strategy without borrowing must explicitly leave it as `None`
    (not applied) — it is not silently filled with 0%."""

    funding: bool
    borrow_apr: OptionalNonNegativeDecimal = None


class AdjustmentsConfig(BaseModel):
    """Toggles split and dividend adjustments independently
    (§3.4 `adjustments{splits, dividends}`)."""

    splits: bool
    dividends: bool


CalendarMode = Literal["session", "24x7"]


class BacktestConfigV2(BaseModel):
    """§3.4 backtest realism contract. Pins the full set of fill-realism
    inputs needed for a single replay run — values are not changed during
    the run (standard-105 principle)."""

    schema_version: Literal["backtest-v2"] = SCHEMA_VERSION
    slippage: SlippageModel
    commission: VenueTierCommission
    latency_ms: int = Field(ge=0)
    partial_fill: PartialFillConfig
    order_types: OrderTypesConfig
    magnifier_tf: Timeframe | None
    costs: CostsConfig
    adjustments: AdjustmentsConfig
    calendar: CalendarMode

    def canonical_json(self) -> str:
        """Canonical serialization for the `reproducibility_key` (BT-9)
        input — sorted keys and fixed separators mean the same value always
        yields the same byte sequence. The hash computation itself is
        BT-9's (`domain/reproducibility.py`) responsibility and is not
        done here."""

        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
