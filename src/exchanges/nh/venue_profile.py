"""L4-04 — NH 국내주식(KR_EQUITY) capability 프로파일 + 심볼 레지스트리 스냅샷.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E(§138 행), §3.2, §9 L4-04.

편차(§3.2 원문 대비): 스펙 §3.2는 "NH `supports_modify=False`,
`supports_cancel='UNVERIFIED'`(02e §3 추정 엔드포인트)"라고 적지만, 이는
`02e_nh_api_spec_v1.md` 최초 작성 시점(엔드포인트 추정)의 값이다.
`nh/trading_mixin.py`(task-114, 2026-09-03 재조사)가 정본으로 지목된
`nhplug-sdk` `openapi.json`을 직접 내려받아 `POST /krstock/order/v1/modify`,
`POST /krstock/order/v1/cancel`이 실제 존재하는 경로임을 확인했고, 그
결과가 이미 코드로 구현돼 있다(`cancel_order`/`modify_order`가
`NotImplementedError`가 아니라 실 요청을 보낸다). "존재하는 엔드포인트"와
"AIOS가 실계좌로 왕복 검증했다"는 다른 축이라 `supports_modify=True`/
`supports_cancel="YES"`로 갱신하되 `verified`는 여전히 `"DOC_ONLY"`로
유지한다(§10 정직 표기 — 문서/스펙 확인이지 라이브 왕복 확인이 아님).

`price_tick`/`qty_lot`/`min_notional`(005930): KIS와 동일 근거(KRX
호가가격단위 공개 규정 — 브로커 무관 거래소 규칙) — `kis/venue_profile.py`
docstring 참고. 이 세션에서 실시간 조회하지 않았으므로 `verified=False`
(§9 L4-04 DoD c, 적대적 테스트 대상).
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
