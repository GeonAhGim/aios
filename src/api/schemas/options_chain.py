"""DC-26 — option-chain transport contracts.

Greeks are deliberately absent from ``OptionContract``.  The chain endpoint
returns market/reference data only; IND owns Greek calculation and callers
request it through ``greeks_indicator_ref``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class OptionContractView(BaseModel, frozen=True):
    instrument_id: UUID
    underlying_id: UUID
    symbol: str
    expiry: AwareDatetime
    strike: Decimal = Field(gt=Decimal("0"))
    option_right: Literal["CALL", "PUT"]
    contract_multiplier: Decimal = Field(gt=Decimal("0"))
    bid: Decimal | None = Field(default=None, ge=Decimal("0"))
    ask: Decimal | None = Field(default=None, ge=Decimal("0"))
    observed_at: AwareDatetime


class OptionChainView(BaseModel, frozen=True):
    underlying_id: UUID
    underlying_symbol: str
    as_of: AwareDatetime
    contracts: list[OptionContractView]
    # IND is the sole owner of Greek calculation.  This is a reference to an
    # indicator definition/run, never a locally calculated value.
    greeks_indicator_ref: str | None = None


__all__ = ["OptionChainView", "OptionContractView"]
