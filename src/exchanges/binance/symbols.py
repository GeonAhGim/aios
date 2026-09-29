"""task-8078 (AUDIT_2026-09-26_order_path.md F5/§1) -- Binance symbol conversion,
delegated to `symbol_normalizer` (LA-7).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-22.

Audit finding (task-7978, F5/§1) -- `symbol_normalizer.py` had no
`Venue.BINANCE` branch and this file did not exist, so
`trading_mixin.py::place_order` passed `order.symbol` to Binance
unconverted and unvalidated -- a design gap, not an implementation bug
(BR-22d never specified a conversion step). Same precedent as
`src/exchanges/bitget/symbols.py` (LA-19) and `src/exchanges/okx/symbols.py`
(BR-21): this module does not implement the conversion rule itself, it
only delegates to `symbol_normalizer` so the order path and the query path
cannot drift apart (FULL_AUDIT_2026-09-02.md §7).

Binance's raw symbol ("BTCUSDT") has the same concatenated-no-separator
shape as Bitget's, so `to_venue`/`to_canonical` route `Venue.BINANCE`
through the same crypto branch as `Venue.BITGET` -- see
`symbol_normalizer.py`'s module docstring. A canonical string missing the
"/" separator (e.g. an already-raw Binance symbol passed where canonical is
expected) is rejected, not silently normalized -- same fail-closed contract
as OKX's `to_okx_symbol`/`from_okx_symbol`.
"""

from __future__ import annotations

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_canonical as _to_canonical,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_venue as _to_venue,
)

__all__ = ["to_binance_symbol", "from_binance_symbol"]


def to_binance_symbol(canonical_symbol: str) -> str:
    """ "BTC/USDT" -> "BTCUSDT" """
    return _to_venue(Venue.BINANCE, canonical_symbol)


def from_binance_symbol(binance_symbol: str) -> str:
    """ "BTCUSDT" -> "BTC/USDT" """
    return _to_canonical(Venue.BINANCE, binance_symbol)
