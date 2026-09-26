"""BR-21 (task-7868) -- OKX symbol conversion, delegated to `symbol_normalizer` (LA-7).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21.

Review REJECT (7802) -- `trading_mixin.py` sent canonical "BASE/QUOTE"
(e.g. "BTC/USDT") to OKX unconverted instead of its instId format
"BASE-QUOTE" (e.g. "BTC-USDT"), which would have failed 100% of live
orders. Same as `src/exchanges/bitget/symbols.py` (LA-19): this module does
not implement the rule itself, it only delegates to `symbol_normalizer` --
if an adapter's own conversion and the LA-7 single rule drift apart (e.g.
the query path gains a new quote and the order path forgets it), that
drift is exactly the incident class FULL_AUDIT_2026-09-02.md §7 warns
against.
"""

from __future__ import annotations

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_canonical as _to_canonical,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_venue as _to_venue,
)

__all__ = ["to_okx_symbol", "from_okx_symbol"]


def to_okx_symbol(canonical_symbol: str) -> str:
    """ "BTC/USDT" -> "BTC-USDT" """
    return _to_venue(Venue.OKX, canonical_symbol)


def from_okx_symbol(okx_symbol: str) -> str:
    """ "BTC-USDT" -> "BTC/USDT" """
    return _to_canonical(Venue.OKX, okx_symbol)
