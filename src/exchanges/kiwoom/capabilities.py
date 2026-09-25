"""BR-23(task-7569) — Kiwoom capability declarations: the coarse
`ExchangeCapability` (`get_capabilities()`) and the precise
`VenueCapabilityProfile` (`venue_profile()`), per
`docs/exchanges/ADDING_AN_EXCHANGE.md` §2.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A/§3.2, §9 L4-04.

`verified="DOC_ONLY"` on `KIWOOM_KR_EQUITY_PROFILE` is an explicit task
instruction and reflects the profile's honest state: fields below are
best-effort assumptions mirroring KIS/NH (same Korean-equity REST-broker
shape), not values confirmed against Kiwoom's own published API reference in
this session (see `auth.py`'s module docstring for the full caveat).
`price_tick`/`qty_lot`/`min_notional` are left empty rather than guessed —
an invented tick/lot value is exactly the kind of "guessed implementation"
CLAUDE.md §3 asks to avoid; a follow-up leaf must populate these from a real
KRX/Kiwoom source before order validation can rely on them for this venue.

`supports_modify=False`/`supports_cancel="UNVERIFIED"`/`supports_ws_orders=
False` — this leaf (step (b): auth + market data) implements no trading or
websocket path at all (account_mixin/trading_mixin/websocket.py are sibling
leaves task-7570/7571/7572), so the profile must not claim capabilities this
leaf provides no code for.
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.exchanges.common.types import ExchangeCapability
from src.services.oms.domain.venue_profile import (
    MarketHours,
    TimeoutBudget,
    VenueCapabilityProfile,
)

VENUE = "kiwoom"

KIWOOM_CAPABILITY = ExchangeCapability(
    exchange_name=VENUE,
    supported_asset_classes=[AssetClass.KR_EQUITY, AssetClass.KR_ETF],
    supports_spot=True,
    supports_futures=False,
    supports_options=False,
    supports_leverage=False,
    # websocket.py (task-7572) is a sibling leaf, not present here yet —
    # subscribe_ticker_stream() stays an explicit `_unsupported()` signal
    # (factory.py), so this must not claim True ahead of that leaf.
    supports_websocket=False,
    max_leverage=Decimal("1"),
    reference_feed_coverage="low",
    # Kiwoom publishes a separate mock/paper trading endpoint (see auth.py's
    # PAPER_BASE_URL) — same DOC_ONLY caveat as the rest of this module.
    has_official_sandbox=True,
)

KIWOOM_KR_EQUITY_PROFILE = VenueCapabilityProfile(
    venue=VENUE,
    asset_classes=[AssetClass.KR_EQUITY, AssetClass.KR_ETF],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"DAY"},
    supports_client_order_id=False,
    client_order_id_max_len=0,
    client_order_id_charset="",
    id_policy="DAILY_SEQUENCE",
    supports_modify=False,
    supports_cancel="UNVERIFIED",
    supports_ws_orders=False,
    supports_batch=False,
    price_tick={},
    qty_lot={},
    min_notional={},
    rate_limits={"query": (5, 5)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=MarketHours(open_time="09:00", close_time="15:30", timezone="Asia/Seoul"),
    max_open_orders_per_symbol=50,
    verified="DOC_ONLY",
)
