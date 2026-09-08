"""L4-04 -- KIS domestic equity (KR_EQUITY) capability profile + symbol registry snapshot.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E (§135 row), §3.2
("confirmed value: KIS supports_client_order_id=False, id_policy=DAILY_SEQUENCE"), §9 L4-04.

Value provenance (honest labeling, §10 principle):
- `supports_client_order_id=False`, `id_policy="DAILY_SEQUENCE"`: spec §3.2
  confirmed value -- `kis/trading_mixin.py` actually sends `client_order_id=""`
  (KIS has no such concept, and ODNO is reused per day, U6).
- `order_types`/`supports_modify`: reflects `kis/trading_mixin.py`'s
  `_order_division` (00=limit/01=market) and the `order-rvsecncl`
  (combined modify/cancel endpoint) implementation as-is -- this is the
  real path the code already calls.
- `market_hours` (09:00-15:30 KST): KRX's published regular-session rule --
  a broker-agnostic exchange rule, so identical for both KIS and NH.
- `rate_limits`: same source as `kis/rate_profile.py` (BR-2) (DOC_ONLY,
  20 requests/sec in production) -- carried over as-is rather than
  re-derived in this module (D4 reuse principle).
- `price_tick`/`qty_lot`/`min_notional` (005930 Samsung Electronics): cites
  KRX's published tick-size rule (price-band step table) -- **not queried
  live in this session**. Since the tick changes with the day's price
  band, the snapshot-time price would need to be reconfirmed for this to
  be truly valid -- hence `verified=False` (§9 L4-04 DoD c, subject to
  adversarial testing). lot=1 is KRX's published regular-market trading
  unit since 2014 (broker-agnostic) -- this value is a price-independent
  constant, so `verified=True`. min_notional=0 reflects the published rule
  that KRX sets no separate minimum order amount for domestic equity cash
  trading.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.services.oms.domain.symbol_registry import SymbolRegistry, SymbolSnapshot
from src.services.oms.domain.venue_profile import (
    MarketHours,
    TimeoutBudget,
    VenueCapabilityProfile,
)

VENUE = "kis"

_KRX_TICK_RULE_URL = (
    "https://law.krx.co.kr (유가증권시장 업무규정 시행세칙 별표 - 호가가격단위)"
)

# 005930 (Samsung Electronics) tick follows the price-band step table --
# verified=False (§9 L4-04 DoD c) since the snapshot-day price was not
# queried live.
KIS_SYMBOL_SNAPSHOTS: dict[str, SymbolSnapshot] = {
    "005930.KS": SymbolSnapshot(
        venue_symbol="005930",
        tick=Decimal("100"),
        lot=Decimal("1"),
        min_notional=Decimal("0"),
        quote_ccy="KRW",
        source_url=_KRX_TICK_RULE_URL,
        fetched_at=date(2026, 9, 9),
        verified=False,
    ),
}


def register_symbols(registry: SymbolRegistry) -> None:
    """Only place `SymbolRegistry.register_snapshot` is called for KIS
    (L4-04 DoD a)."""
    for canonical, snapshot in KIS_SYMBOL_SNAPSHOTS.items():
        registry.register_snapshot(canonical, VENUE, snapshot)


KIS_KR_EQUITY_PROFILE = VenueCapabilityProfile(
    venue=VENUE,
    asset_classes=[AssetClass.KR_EQUITY, AssetClass.KR_ETF],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"DAY"},
    supports_client_order_id=False,
    client_order_id_max_len=0,
    client_order_id_charset="",
    id_policy="DAILY_SEQUENCE",
    supports_modify=True,
    supports_cancel="YES",
    supports_ws_orders=False,
    supports_batch=False,
    price_tick={"005930.KS": Decimal("100")},
    qty_lot={"005930.KS": Decimal("1")},
    min_notional={"005930.KS": Decimal("0")},
    rate_limits={"order": (20, 20), "query": (20, 20)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=MarketHours(open_time="09:00", close_time="15:30", timezone="Asia/Seoul"),
    max_open_orders_per_symbol=50,
    verified="DOC_ONLY",
)
