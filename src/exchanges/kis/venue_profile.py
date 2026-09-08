"""L4-04 — KIS 국내주식(KR_EQUITY) capability 프로파일 + 심볼 레지스트리 스냅샷.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E(§135 행), §3.2
("확정값: KIS supports_client_order_id=False, id_policy=DAILY_SEQUENCE"), §9 L4-04.

값의 출처(정직 표기, §10 원칙):
- `supports_client_order_id=False`, `id_policy="DAILY_SEQUENCE"`: 스펙 §3.2
  확정값 — `kis/trading_mixin.py`가 실제로 `client_order_id=""`를 쓴다
  (KIS는 이 개념이 없고 ODNO가 일자별로 재사용됨, U6).
- `order_types`/`supports_modify`: `kis/trading_mixin.py`의 `_order_division`
  (00=지정가/01=시장가)과 `order-rvsecncl`(정정·취소 통합 엔드포인트) 구현을
  그대로 반영 — 코드가 이미 호출하는 실 경로다.
- `market_hours`(09:00-15:30 KST): KRX 정규장 공개 규정 — 브로커 무관 거래소
  규칙이라 KIS/NH 둘 다 동일.
- `rate_limits`: `kis/rate_profile.py`(BR-2)와 동일 출처(DOC_ONLY, 실전
  20건/초) — 이 모듈에서 재도출하지 않고 그대로 옮긴다(D4 재사용 원칙).
- `price_tick`/`qty_lot`/`min_notional`(005930 삼성전자): KRX 호가가격단위
  공개 규정(가격대별 단계표) 인용 — **이 세션에서 실시간 조회하지 않음**.
  당일 가격대에 따라 tick이 달라지므로 스냅샷 시점 가격을 재확인해야
  진짜 유효하다 — 그래서 `verified=False`(§9 L4-04 DoD c, 적대적 테스트
  대상). lot=1은 2014년 이후 KRX 정규시장 매매수량단위(공개 규정, 브로커
  무관) — 이 값은 가격 무관 상수라 `verified=True`. min_notional=0은 KRX가
  국내주식 현금매매에 별도 최소주문금액을 두지 않는다는 공개 규정.
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

# 005930(삼성전자) tick은 가격대별 단계표를 따른다 — 스냅샷 당일 가격을
# 실시간 조회하지 않았으므로 verified=False(§9 L4-04 DoD c).
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
