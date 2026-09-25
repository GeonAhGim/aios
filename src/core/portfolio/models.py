"""03_core_modules_v1.1.md#§3.6 — AllocationDecision."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class AllocationDecision(BaseModel):
    symbol: str
    strategy_id: str
    approved_quantity: Decimal
    capital_pct: Decimal  # 8.2-B revalidation of strategy-level capital allocation limits (FD-8.3)
