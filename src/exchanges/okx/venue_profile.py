"""BR-21b -- OKX spot capability profile constant.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21, §3.2,
docs/exchanges/ADDING_AN_EXCHANGE.md step 2.

Value provenance (honest labeling, same convention as
`bitget/venue_profile.py`'s module docstring):
- `order_types`/`time_in_force`: matches this leaf's actual scope --
  `okx/trading_mixin.py` only maps MARKET/LIMIT order types (its module
  docstring's "Unverified scope" note) and only ever sends `tdMode="cash"`
  (spot). OKX's REST docs list additional TIF values (`post_only`, `fok`,
  `ioc`) that this profile deliberately excludes -- declaring a TIF this
  package cannot actually place would let a caller believe the order will
  behave a way `trading_mixin.py` cannot deliver.
- `price_tick`/`qty_lot`/`min_notional`: **unverified** (DOC_ONLY) -- OKX
  publishes exact per-instrument tick/lot sizes via
  `GET /api/v5/public/instruments`, but this leaf does not call that
  endpoint (no live key exercised it yet, same posture as Bitget's
  unverified `client_order_id_max_len`). Placeholder BTC-USDT/ETH-USDT
  values below are a conservative estimate from OKX's public instrument
  browser, not a verified API response.
- `client_order_id_max_len=32`: OKX's documented `clOrdId` max length
  (official docs, "Order Book Trading" parameter table).
- `supports_ws_orders=True`: unlike Bitget's `False` (unverified private-WS
  login), OKX's private `orders` WS channel is actually wired end-to-end in
  this same PR (`okx/websocket.py::OKXWebSocketMixin.subscribe_order_stream`)
  -- declaring `True` here without that wiring would repeat exactly the
  KIS/NH defect this doc's step 2 warns against.
- `rate_limits`: **unverified** -- conservative estimate; OKX publishes
  precise per-endpoint limits but this leaf does not measure them live.

`verified` stays `"DOC_ONLY"` until a live OKX demo account round trip
(place/cancel/get + a rejected invalid order) both succeed, per the same
rule `bitget/venue_profile.py` documents -- changing this value without
that verification is not permitted.
"""

from __future__ import annotations

from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile

VENUE = "okx"

OKX_SPOT_PROFILE = VenueCapabilityProfile(
    venue=VENUE,
    asset_classes=[AssetClass.CRYPTO],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"GTC", "IOC", "FOK"},
    supports_client_order_id=True,
    client_order_id_max_len=32,
    client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    id_policy="STABLE",
    supports_modify=True,
    supports_cancel="YES",
    supports_ws_orders=True,
    supports_batch=True,
    price_tick={"BTC-USDT": Decimal("0.1"), "ETH-USDT": Decimal("0.01")},
    qty_lot={"BTC-USDT": Decimal("0.00000001"), "ETH-USDT": Decimal("0.000001")},
    min_notional={"BTC-USDT": Decimal("1"), "ETH-USDT": Decimal("1")},
    rate_limits={"order": (5, 10), "query": (10, 20)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=None,
    max_open_orders_per_symbol=200,
    verified="DOC_ONLY",
)
