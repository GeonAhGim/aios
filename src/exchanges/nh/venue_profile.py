"""L4-04 -- NH domestic equity (KR_EQUITY) capability profile + symbol registry snapshot.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E (§138 row), §3.2, §9 L4-04.

Deviation (from the §3.2 original text): spec §3.2 states "NH
`supports_modify=False`, `supports_cancel='UNVERIFIED'` (02e §3 assumed
endpoint)", but that reflects the value from when `02e_nh_api_spec_v1.md`
was first written (endpoint assumed, not confirmed). `nh/trading_mixin.py`
(task-114, re-investigated 2026-09-03) downloaded the `openapi.json`
designated as the authoritative source directly from `nhplug-sdk` and
confirmed that `POST /krstock/order/v1/modify` and
`POST /krstock/order/v1/cancel` are indeed existing paths, and that
result is already implemented in code (`cancel_order`/`modify_order` send
real requests rather than raising `NotImplementedError`). "The endpoint
exists" and "AIOS has round-trip verified it against a live account" are
different axes, so `supports_modify=True`/`supports_cancel="YES"` are
updated accordingly, while `verified` still stays `"DOC_ONLY"` (§10
honest labeling -- this confirms documentation/spec, not a live round
trip).

`price_tick`/`qty_lot`/`min_notional` (005930): same basis as KIS (KRX's
published tick-size rule -- a broker-agnostic exchange rule) -- see the
`kis/venue_profile.py` docstring. Not queried live in this session, so
`verified=False` (§9 L4-04 DoD c, subject to adversarial testing).
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

VENUE = "nh"

_KRX_TICK_RULE_URL = (
    "https://law.krx.co.kr (유가증권시장 업무규정 시행세칙 별표 - 호가가격단위)"
)

NH_SYMBOL_SNAPSHOTS: dict[str, SymbolSnapshot] = {
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
    """Only place `SymbolRegistry.register_snapshot` is called for NH
    (L4-04 DoD a)."""
    for canonical, snapshot in NH_SYMBOL_SNAPSHOTS.items():
        registry.register_snapshot(canonical, VENUE, snapshot)


NH_KR_EQUITY_PROFILE = VenueCapabilityProfile(
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
    rate_limits={"order": (1, 1), "query": (1, 1)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=MarketHours(open_time="09:00", close_time="15:30", timezone="Asia/Seoul"),
    max_open_orders_per_symbol=50,
    verified="DOC_ONLY",
)
