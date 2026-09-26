"""BR-22b -- Binance spot capability profile constants.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E, §3.2;
      docs/exchanges/ADDING_AN_EXCHANGE.md step 2 (venue-profile convention
      -- `factory.py`'s `BinanceAdapter.venue_profile()` returns this
      constant; see that file for the actual wiring this doc warns not to
      skip).

Value provenance (honest labeling, §10 principle) -- all DOC_ONLY, no live
account used in this session:
- `price_tick`/`qty_lot`/`min_notional` (BTC/USDT, ETH/USDT): the official
  `GET /api/v3/exchangeInfo` documents these per-symbol via the
  PRICE_FILTER.tickSize / LOT_SIZE.stepSize / MIN_NOTIONAL.minNotional
  filters (github.com/binance/binance-spot-api-docs, `rest-api.md`
  "Filters"). The values below are the commonly published current filter
  values for these two pairs -- not queried live in this session, hence
  `verified="DOC_ONLY"` (same posture as bitget/venue_profile.py before its
  live-measurement leaf).
- `client_order_id_max_len=36`, charset: official docs, `newClientOrderId`
  field description ("up to 36 characters," alphanumeric plus `-_.`).
- `supports_ws_orders=False`: Binance does publish an authenticated
  WebSocket API for order placement (`ws-api.md`), but this adapter's
  `websocket.py` only covers the ticker/depth/user-data streams, not that
  order-entry path -- declaring True here without a real subscribe path
  would fail the cross-check `check_exchange_spi.py` runs (ADDING_AN_EXCHANGE.md
  §2: declared capability must match actual implementation).
- `rate_limits`: official docs' default REQUEST_WEIGHT (1200/min) and
  ORDERS (10/sec, 100000/day) limits, carried over as a conservative
  per-second estimate -- unverified against a real key.
- `time_in_force={"GTC"}`: `trading_mixin.py`'s `place_order`/`modify_order`
  only ever send `timeInForce="GTC"` for LIMIT orders (§10 -- declare only
  what the code actually sends, not the full set Binance's API accepts).
- `market_hours=None`: crypto spot trades 24/7 (no exchange calendar, same
  convention as bitget/venue_profile.py).
"""

from __future__ import annotations

from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile

VENUE = "binance"

BINANCE_SPOT_PROFILE = VenueCapabilityProfile(
    venue=VENUE,
    asset_classes=[AssetClass.CRYPTO],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"GTC"},
    supports_client_order_id=True,
    client_order_id_max_len=36,
    client_order_id_charset=(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
    ),
    id_policy="STABLE",
    supports_modify=True,
    supports_cancel="YES",
    supports_ws_orders=False,
    supports_batch=False,
    price_tick={"BTC/USDT": Decimal("0.01"), "ETH/USDT": Decimal("0.01")},
    qty_lot={"BTC/USDT": Decimal("0.00001"), "ETH/USDT": Decimal("0.0001")},
    min_notional={"BTC/USDT": Decimal("5"), "ETH/USDT": Decimal("5")},
    rate_limits={"order": (10, 50), "query": (20, 40)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=None,
    max_open_orders_per_symbol=200,
    verified="DOC_ONLY",
)
