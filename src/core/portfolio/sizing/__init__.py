"""L4_strategy_portfolio_backtest_v1.0.md#§2 rows 90~94 — sizing package.

`SizingResult`/`PortfolioSizingError` conceptually belong to the §2 row 87
`core.portfolio.models` MINOR extension (the SizingResult DTO), but L17's
(task-2452) actual commit did not make that extension, and this leaf's
(L19, task-2525) file scope is pinned to `sizing/**` — so the sizing package
owns its own public result type and shared exception directly here (the
out-of-scope `models.py` is left untouched).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel

from src.core.portfolio.config import SizingMethod
from src.core.risk.hashing import canonical_json, sha256_hex

SCHEMA_VERSION = "sizing-v1"
HUNDRED = Decimal("100")

_T = TypeVar("_T")


class PortfolioSizingError(ValueError):
    """Sizing input violates the contract — the fail-closed base exception."""

    code = "PORTFOLIO_SIZING_ERROR"


class SizingInputMissingError(PortfolioSizingError):
    """A required input is `None` — rejected rather than substituted with 0/a default."""

    code = "PORTFOLIO_SIZING_INPUT_MISSING"

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"{self.code}: required input '{field}' is missing")


class SizingInputInvalidError(PortfolioSizingError):
    """The input value is present but outside the domain the formula defines
    (e.g. `price <= 0`)."""

    code = "PORTFOLIO_SIZING_INPUT_INVALID"

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f"{self.code}: {field} {reason}")


class SizingResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    quantity: Decimal
    weight_pct: Decimal
    method: SizingMethod
    inputs_hash: str


def require(value: _T | None, field: str) -> _T:
    """Filters out `None` input here so each formula file doesn't repeat the check."""
    if value is None:
        raise SizingInputMissingError(field)
    return value


def require_positive(value: Decimal, field: str) -> Decimal:
    if value <= 0:
        raise SizingInputInvalidError(field, "must be > 0")
    return value


def hash_inputs(method: SizingMethod, **fields: Any) -> str:
    """R1: the same formula with the same input always yields the same `inputs_hash`."""
    payload = {"method": method.value, **fields}
    return sha256_hex(canonical_json(payload))
