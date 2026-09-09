"""L4-30 -- Bitget spot capability profile constants.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E, §9 L4-30
      (ADR-2026-09-06-G §11 -- strengthened DoD for "concrete rejection input")

Value provenance (honest labeling, §10 principle):
- `price_tick`/`qty_lot`/`min_notional`/`max_open_orders_per_symbol`:
  **measured live** on 2026-09-08 via
  `GET /api/v2/spot/public/symbols?symbol=<BTC|ETH>USDT` (a public
  endpoint -- no auth required; `pricePrecision`/`quantityPrecision` digit
  counts converted to negative powers of ten, `minTradeUSDT`/
  `orderQuantity` carried over as-is). These values were already confirmed
  live, independent of the place/cancel/get round trip.
- `client_order_id_max_len=40`: **unverified** (U2, item 04 §11.3) --
  documentation-only basis.
- `rate_limits`: **unverified** -- no official limits documentation could
  be found, so this is a conservative estimate.
- `supports_ws_orders=False`: U3 (02b §6 self-admitted) -- the private WS
  `orders` channel's login/signature scheme is unverified live, so the
  default fails closed to the REST polling path.
- `time_in_force`: declares only the subset of Bitget spot's official
  `force` parameter values (gtc/ioc/fok) that overlap with the AIOS
  contract (`SubmitOrderCommand.time_in_force`).

`verified` follows the **weakest** of these three lines of evidence -- it
stays `"DOC_ONLY"` until the place/cancel/get round trip AND rejection of
an invalid order **both** succeed on the Bitget demo (unverified as of
this commit due to the absence of BITGET_API_PASSPHRASE -- see the
docstring of
`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`). Only
update to `"LIVE_VERIFIED"` in the commit where both DoDs actually pass --
changing the value without passing them is forbidden
(ADR-2026-09-06-G §11).

UTA(Unified Account) demo trading (task-2514, 2026-09-09 official-doc
research -- unverified live): demo trading is supported under UTA v3 too,
but it requires a **separate Demo API Key** created specifically for demo
trading -- an existing live-account key does not become a demo key just by
adding the `paptrading: 1` header (this matches the 2026-09-09 real-key
measurement in the task spec: sending `paptrading: 1` with a non-demo key
returned body code 40099 "exchange environment is incorrect", classified
as AUTH in `error_codes.py`). The header name and REST base URL are
unchanged from Classic (`account_mode.py`/`adapter.py` reuse `_headers()`
as-is); only the account-mode-specific endpoint paths differ (see
`docs/design/02d_bitget_uta_v3_spec_v1.md`).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.services.oms.domain.symbol_registry import SymbolRegistry, SymbolSnapshot
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile

VENUE = "bitget"

_SNAPSHOT_SOURCE = "https://www.bitget.com/api-doc/spot/market/Get-Symbols"
_SNAPSHOT_DATE = date(2026, 9, 8)

# L4-04 — per-symbol registry snapshot (distinct from `BITGET_SPOT_PROFILE`
# below, which is capability/limit metadata for the whole venue). Values are
# the same 2026-09-08 `GET /api/v2/spot/public/symbols` response the
# docstring below cites for `price_tick`/`qty_lot`/`min_notional` —
# `verified=True` here because those two symbols were read from that actual
# response body, not estimated.
BITGET_SYMBOL_SNAPSHOTS: dict[str, SymbolSnapshot] = {
    "BTC/USDT": SymbolSnapshot(
        venue_symbol="BTCUSDT",
        tick=Decimal("0.01"),
        lot=Decimal("0.000001"),
        min_notional=Decimal("1"),
        quote_ccy="USDT",
        source_url=_SNAPSHOT_SOURCE,
        fetched_at=_SNAPSHOT_DATE,
        verified=True,
    ),
    "ETH/USDT": SymbolSnapshot(
        venue_symbol="ETHUSDT",
        tick=Decimal("0.01"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("1"),
        quote_ccy="USDT",
        source_url=_SNAPSHOT_SOURCE,
        fetched_at=_SNAPSHOT_DATE,
        verified=True,
    ),
}


def register_symbols(registry: SymbolRegistry) -> None:
    """Only place `SymbolRegistry.register_snapshot` is called for Bitget —
    production callers must go through this (L4-04 DoD a: eliminates the
    previous 0-hit `SymbolRegistry()` production-instantiation gap)."""
    for canonical, snapshot in BITGET_SYMBOL_SNAPSHOTS.items():
        registry.register_snapshot(canonical, VENUE, snapshot)


BITGET_SPOT_PROFILE = VenueCapabilityProfile(
    venue="bitget",
    asset_classes=[AssetClass.CRYPTO],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"GTC", "IOC", "FOK"},
    supports_client_order_id=True,
    client_order_id_max_len=40,
    client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    id_policy="STABLE",
    supports_modify=True,
    supports_cancel="YES",
    supports_ws_orders=False,
    supports_batch=True,
    price_tick={"BTC/USDT": Decimal("0.01"), "ETH/USDT": Decimal("0.01")},
    qty_lot={"BTC/USDT": Decimal("0.000001"), "ETH/USDT": Decimal("0.0001")},
    min_notional={"BTC/USDT": Decimal("1"), "ETH/USDT": Decimal("1")},
    rate_limits={"order": (5, 10), "query": (10, 20)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=None,
    max_open_orders_per_symbol=200,
    verified="DOC_ONLY",
)
