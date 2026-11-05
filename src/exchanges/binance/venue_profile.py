"""BR-22b -- Binance spot capability profile constant.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 2.

Value provenance (honest labeling): everything here is **DOC_ONLY** --
nothing was measured against a live or testnet Binance account.
- `order_types`/`time_in_force`: only what `binance/trading_mixin.py` can
  place (MARKET/LIMIT; GTC/IOC/FOK).
- `price_tick`/`qty_lot`/`min_notional`: reuse `binance/capabilities.py`
  (BTCUSDT/ETHUSDT only; other symbols are unvalidated, not guessed).
- `client_order_id_max_len=36` / charset `[A-Za-z0-9-_]`: documented
  `newClientOrderId` pattern `^[a-zA-Z0-9-_]{1,36}$`.
- `supports_ws_orders=False`: no Binance private WS is wired.
- `rate_limits`: conservative estimate, unverified.
- `supports_batch=False`: no batch endpoint is wired.

`verified` stays "DOC_ONLY" until a testnet place/cancel/get round trip
and a rejected invalid order both succeed.
"""

from __future__ import annotations

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.exchanges.binance.capabilities import MIN_NOTIONAL, PRICE_TICK, QTY_LOT
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile

VENUE = "binance"

BINANCE_SPOT_PROFILE = VenueCapabilityProfile(
    venue=VENUE,
    asset_classes=[AssetClass.CRYPTO],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"GTC", "IOC", "FOK"},
    supports_client_order_id=True,
    client_order_id_max_len=36,
    client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
    id_policy="STABLE",
    supports_modify=True,
    supports_cancel="YES",
    supports_ws_orders=False,
    supports_batch=False,
    price_tick=dict(PRICE_TICK),
    qty_lot=dict(QTY_LOT),
    min_notional=dict(MIN_NOTIONAL),
    rate_limits={"order": (5, 10), "query": (10, 20)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=None,
    max_open_orders_per_symbol=200,
    verified="DOC_ONLY",
)
