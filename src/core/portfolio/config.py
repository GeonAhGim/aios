"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 89 — PortfolioConfig + config_hash.

Versioned per-execution sizing config contract (R2) that will be stored in
`strategy_executions.portfolio_config` JSONB. The actual JSONB read/write
wiring belongs to L23 — this file owns only the pure type and the hash
recipe.
"""
from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator

from src.core.risk.hashing import canonical_json, sha256_hex

SCHEMA_VERSION = "pcfg-v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _reject_float(value: Any) -> Any:
    """Reject a python float on a Decimal field before pydantic coerces it.

    pydantic's default lax mode silently converts float -> Decimal, which
    would let floating-point error leak into money/percentage math (R2,
    doc 105 principle). Callers must pass Decimal/str/int explicitly.
    """
    if isinstance(value, float):
        raise ValueError("float is not accepted here — pass Decimal")
    return value


class SizingMethod(str, Enum):
    FIXED_FRACTIONAL = "FIXED_FRACTIONAL"
    VOLATILITY_TARGET = "VOLATILITY_TARGET"
    KELLY_CAPPED = "KELLY_CAPPED"
    RISK_PARITY = "RISK_PARITY"


class CostModelRef(BaseModel):
    """A reference key, not a reimplementation of
    `foundation.backtest.domain.models.CostModel` — carries only the model
    identifier plus that instance's `cost_model_hash` (§C: no duplicate
    context). Consumers that need the actual cost model values look it up
    by `model_id` in the module that owns it."""

    model_id: str
    cost_model_hash: str

    @field_validator("cost_model_hash")
    @classmethod
    def _check_hash_shape(cls, value: str) -> str:
        if not _SHA256_HEX_RE.fullmatch(value):
            raise ValueError("cost_model_hash must be a lowercase sha256 hex digest")
        return value


class PortfolioConfig(BaseModel):
    schema_version: str = SCHEMA_VERSION
    method: SizingMethod
    fraction_pct: Decimal = Decimal("100")
    target_vol_pct: Decimal | None = None
    kelly_cap_pct: Decimal | None = None
    rebalance_band_pct: Decimal = Decimal("5")
    min_trade_notional: Decimal
    cost_model: CostModelRef

    @field_validator(
        "fraction_pct",
        "target_vol_pct",
        "kelly_cap_pct",
        "rebalance_band_pct",
        "min_trade_notional",
        mode="before",
    )
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float(value)

    @field_validator("min_trade_notional")
    @classmethod
    def _check_non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("min_trade_notional must be >= 0")
        return value

    def config_hash(self) -> str:
        """§3.0 `config_hash` — sha256 of the canonical JSON of every field.

        Reuses `src.core.risk.hashing` instead of inventing a new hash
        recipe (R1 — identical values must always yield identical bytes).
        """
        return sha256_hex(canonical_json(self.model_dump(mode="python")))
