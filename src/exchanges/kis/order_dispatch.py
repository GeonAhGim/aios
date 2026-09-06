"""BR-8(ADR-2026-09-06-I D2, ADR-H D7) — KISAdapter.place_order() 자산군 분기표.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §10 BR (신설)

adapter.py의 300줄 캡을 넘기지 않으려 분기 로직만 분리한다(최소모듈 원칙).
ETF/ETN은 별도 주문 엔드포인트가 없다(etf_mixin.py 참조 — KRX 상장
종목코드 매매라 domestic_stock의 order-cash를 그대로 쓴다). 국내/해외
선물옵션은 각자의 place_*_order(order)가 order 하나만 받으므로 그대로
위임한다.

이전에는 `KISAdapter.place_order`가 `KISTradingMixin.place_order`(domestic
전용)로 고정돼 있어, get_capabilities()만 넓히면 해외주식·선물옵션 Order가
조용히 국내주식 파라미터로 잘못된 시장에 나갈 뻔했다 — 이 모듈이 그 실제
분기를 채운다(BR-8 핵심 수정).
"""
from __future__ import annotations

from typing import Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order
from src.exchanges.kis.trading_mixin import KISTradingMixin

_DOMESTIC_CASH_ASSET_CLASSES = frozenset(
    {AssetClass.KR_EQUITY, AssetClass.KR_ETF, AssetClass.KR_ETN}
)
_DOMESTIC_DERIVATIVE_ASSET_CLASSES = frozenset(
    {AssetClass.KR_FUTURES, AssetClass.KR_OPTION}
)
_OVERSEAS_DERIVATIVE_ASSET_CLASSES = frozenset(
    {AssetClass.OVERSEAS_FUTURES, AssetClass.OVERSEAS_OPTION}
)
_OVERSEAS_CASH_ASSET_CLASSES = frozenset(
    {AssetClass.US_EQUITY, AssetClass.US_ETF, AssetClass.US_ETN}
)


def _split_overseas_symbol(symbol: str) -> tuple[str, str]:
    """해외주식/ETF/ETN 주문은 4자리 거래소 코드(OVRS_EXCG_CD, 예: NASD)가
    필수인데(overseas_stock_mixin.py) `Order` 모델에는 그 필드가 없다(01번
    설계가 국내 단일거래소만 가정, 이 리프의 파일 스콥은 trading.py를
    포함하지 않아 모델 확장은 후속 리프 몫). trading_mixin.py가 이미 쓰는
    "{orgno}:{odno}" 합성 관례를 반대 방향으로 재사용해, `ExchangeAdapter.
    place_order()` 진입점에서만 `symbol`을 "{거래소코드}:{종목코드}"
    (예: "NASD:AAPL")로 받는 KIS 전용 편의 규약을 둔다. `place_overseas_
    order()`를 직접 호출하는 기존 경로(거래소 코드를 별도 인자로 받음)는
    영향받지 않는다. 콜론이 없으면 거래소코드를 추측하지 않고 즉시
    거부한다(8.3 fail-closed 원칙 — 틀린 거래소로 조용히 나가는 것보다
    안전)."""
    if ":" not in symbol:
        raise FatalExchangeError(
            "해외주식/ETF/ETN 주문의 symbol은 'OVRS_EXCG_CD:종목코드' 형식이어야 "
            f"합니다(예: 'NASD:AAPL'): {symbol!r}"
        )
    exchange, venue_symbol = symbol.split(":", 1)
    return exchange, venue_symbol


class _DispatchableAdapter(Protocol):
    """`dispatch_place_order`가 `self`(KISAdapter)에 기대하는 최소 계약 —
    실제로는 각 mixin이 제공하는 메서드들이라 KISAdapter 인스턴스면 항상
    만족한다(bitget _OrderReadingClient와 동일 패턴)."""

    async def place_futureoption_order(self, order: Order) -> Order: ...
    async def place_overseas_futureoption_order(self, order: Order) -> Order: ...
    async def place_overseas_order(self, order: Order, exchange: str) -> Order: ...


async def dispatch_place_order(adapter: _DispatchableAdapter, order: Order) -> Order:
    if order.asset_class in _DOMESTIC_CASH_ASSET_CLASSES:
        return await KISTradingMixin.place_order(adapter, order)  # type: ignore[arg-type]
    if order.asset_class in _DOMESTIC_DERIVATIVE_ASSET_CLASSES:
        return await adapter.place_futureoption_order(order)
    if order.asset_class in _OVERSEAS_DERIVATIVE_ASSET_CLASSES:
        return await adapter.place_overseas_futureoption_order(order)
    if order.asset_class in _OVERSEAS_CASH_ASSET_CLASSES:
        exchange, venue_symbol = _split_overseas_symbol(order.symbol)
        overseas_order = order.model_copy(update={"symbol": venue_symbol})
        return await adapter.place_overseas_order(overseas_order, exchange)
    raise FatalExchangeError(
        f"KISAdapter.place_order: capabilities에 없는 자산군"
        f"({order.asset_class.value})입니다 — get_capabilities()보다 먼저 "
        "Validator가 걸러야 하는 주문이 여기 도달했습니다(상위 계층 버그, "
        "fail-closed 방어)."
    )
