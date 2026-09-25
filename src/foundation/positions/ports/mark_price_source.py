"""LB-7 — Mark price supply port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9 LB-7.

The domain/application layer knows only this Protocol; actual implementations
(adapters/candle_mark_price_source.py, LB-14) are unknown (§4 of 71). When a value
cannot be retrieved (including stale data), return `None` instead of substituting `0`
— unrealized PnL is not computed without a mark price (§4 domain/pnl.py).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from src.data.models.base import Money


@runtime_checkable
class MarkPriceSource(Protocol):
    async def mark(self, position_key: str, at: AwareDatetime) -> Money | None:
        """Latest mark price as of `at`. Returns `None` when stale or unavailable
        (POS_MARK_STALE — caller keeps unrealized PnL as None)."""
        ...
