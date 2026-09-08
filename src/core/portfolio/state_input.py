"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 88 — PortfolioStateInput.

Replaces the `current_portfolio_state: dict` accepted by
`PortfolioEngine.allocate`. Existing callers (`run_backtest.py`,
`services/execution_loop/tick.py`) still build a flat dict, so
`from_dict()` accepts that dict as-is — wiring `engine.py` itself is out
of scope for this leaf.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator

from src.core.portfolio.config import PortfolioConfig
from src.foundation.mandates.contracts.v1 import MandateRevisionView

SCHEMA_VERSION = "psi-v1"


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float is not accepted here — pass Decimal")
    return value


class PortfolioAggregate(BaseModel):
    """8.2-C cross-execution exposure aggregate. §2 assigns this type to
    `aggregation.py` (L18), but L18 is only assigned after this leaf (L17)
    lands, and `state_input.py` needs to reference it right now for the
    `exposures` field. So the value type is owned here; L18's
    `aggregation.py` will add only `aggregate()` and import this type
    rather than redefining it."""

    total_equity: Decimal
    per_symbol_pct: dict[str, Decimal]
    per_strategy_pct: dict[str, Decimal]
    total_exposure_pct: Decimal
    cash_pct: Decimal
    as_of: datetime


class PortfolioStateInput(BaseModel):
    schema_version: str = SCHEMA_VERSION
    allocated_capital: Decimal
    position_quantity: Decimal
    current_price: Decimal
    total_equity: Decimal
    cash_available: Decimal
    realized_vol_pct: Decimal | None = None
    win_rate: Decimal | None = None
    avg_win_loss_ratio: Decimal | None = None
    exposures: PortfolioAggregate | None = None
    mandate: MandateRevisionView | None = None
    portfolio_config: PortfolioConfig

    @field_validator(
        "allocated_capital",
        "position_quantity",
        "current_price",
        "total_equity",
        "cash_available",
        "realized_vol_pct",
        "win_rate",
        "avg_win_loss_ratio",
        mode="before",
    )
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float(value)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PortfolioStateInput:
        """Accept the existing `current_portfolio_state` dict (or a superset).

        A missing required key is rejected by pydantic as a
        `ValidationError` — this method does not reinterpret key names or
        silently fill in defaults.
        """
        return cls.model_validate(dict(data))
