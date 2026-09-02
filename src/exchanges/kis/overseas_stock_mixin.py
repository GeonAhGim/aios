"""02d_kis_api_full_spec_v1.md §4 — KISAdapter 해외주식(overseas_stock) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §4, §7(작업 분해 3번)

06번 §6.1-A(자산군 확장 원칙) 재확인 — Phase 1 확정 스콥은 KR_EQUITY뿐
(adapter.py::get_capabilities() 참조), 이 mixin은 사용자 요청("모든
기능")에 따른 API 연동만 제공한다. `ExchangeAdapter` ABC에는 아직
없음(FD-4/8 호출부가 해외주식을 소비하기 전까지 KIS 전용 확장).

이번 조사(WebFetch, github.com/koreainvestment/open-trading-api/
examples_llm/overseas_stock, 2026-09-02)로 실제 예제 코드의 tr_id/
path/파라미터명을 확인했다. **핵심 함정**: 시세조회 거래소코드(EXCD,
3자리, 예: "NAS")와 주문 거래소코드(OVRS_EXCG_CD, 4자리, 예: "NASD")가
서로 다른 코드 체계다 — 하나로 통일하면 안 됨(공식 예제로 직접 확인).
매수/매도 tr_id는 국가마다 다르고, 실전 tr_id는 전부 "T"로 시작해
기존 `_resolve_tr_id()`의 T→V 치환 규칙이 그대로 적용된다(추가 매핑
불필요).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, NamedTuple

from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus


class _MarketCodes(NamedTuple):
    quote_excd: str  # 시세조회용 3자리 코드
    order_excg_cd: str  # 주문용 4자리 코드
    buy_tr_id: str
    sell_tr_id: str


# 국가별 코드/tr_id — 공식 예제(order/order.py) 확인 값. 실전 tr_id 기준,
# 모의투자는 어댑터의 기존 T/J/C→V 치환이 자동 적용된다.
_MARKETS: dict[str, _MarketCodes] = {
    "US": _MarketCodes("NAS", "NASD", "TTTT1002U", "TTTT1006U"),
    "HK": _MarketCodes("HKS", "SEHK", "TTTS1002U", "TTTS1001U"),
    "SH": _MarketCodes("SHS", "SHAA", "TTTS0202U", "TTTS1005U"),
    "SZ": _MarketCodes("SZS", "SZAA", "TTTS0305U", "TTTS0304U"),
    "JP": _MarketCodes("TSE", "TKSE", "TTTS0308U", "TTTS0307U"),
    "VN": _MarketCodes("HSX", "VNSE", "TTTS0311U", "TTTS0310U"),
}


def _market_codes(market: str) -> _MarketCodes:
    codes = _MARKETS.get(market.upper())
    if codes is None:
        raise ValueError(
            f"지원하지 않는 해외주식 시장입니다: {market!r} "
            f"(지원: {', '.join(_MARKETS)})"
        )
    return codes


class KISOverseasStockMixin:
    async def get_overseas_ticker(self, symbol: str, market: str) -> Ticker:
        codes = _market_codes(market)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300",
            params={"AUTH": "", "EXCD": codes.quote_excd, "SYMB": symbol},
        )
        output = raw.get("output", {})
        price = Decimal(output.get("last", "0"))
        return Ticker(
            symbol=symbol,
            exchange="kis",
            price=price,
            bid=price,  # 해외주식 현재가 응답엔 최우선호가가 없음(문서 관례) — 근사치
            ask=price,
            volume_24h=Decimal(output.get("tvol", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )

    async def place_overseas_order(self, order: Order, market: str) -> Order:
        codes = _market_codes(market)
        tr_id = codes.buy_tr_id if order.side == OrderSide.BUY else codes.sell_tr_id
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "OVRS_EXCG_CD": codes.order_excg_cd,
            "PDNO": order.symbol,
            "ORD_QTY": str(order.quantity),
            "OVRS_ORD_UNPR": str(order.price.amount) if order.price is not None else "0",
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "SLL_TYPE": "00" if order.side == OrderSide.SELL else "",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/uapi/overseas-stock/v1/trading/order", tr_id, body=body
        )
        output = raw.get("output", {})
        exchange_order_id = f"{output.get('KRX_FWDG_ORD_ORGNO', '')}:{output.get('ODNO', '')}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    async def cancel_overseas_order(
        self, order_id: str, symbol: str, market: str, *, original_quantity: Decimal
    ) -> bool:
        """미국 시장 기준으로 확인된 tr_id(TTTT1004U)만 신뢰도가 있다 —
        다른 시장의 정정취소 tr_id는 이번 조사에서 확인하지 못해 US와
        동일 tr_id를 임시로 재사용한다(라이브 검증 전까지 확정 아님,
        틀렸다면 거래소가 오류를 반환할 뿐 잘못된 주문이 나가지는
        않는다 — 8.3 원칙)."""
        codes = _market_codes(market)
        orgno, odno = order_id.split(":", 1)
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "OVRS_EXCG_CD": codes.order_excg_cd,
            "PDNO": symbol,
            "ORGN_ODNO": odno,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(original_quantity),
            "OVRS_ORD_UNPR": "0",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/uapi/overseas-stock/v1/trading/order-rvsecncl", "TTTT1004U", body=body
        )
        return bool(raw.get("rt_cd") == "0")

    async def get_overseas_balance(self, market: str) -> list[AccountBalance]:
        codes = _market_codes(market)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            "TTTS3012R",
            params={
                "CANO": self._cano,  # type: ignore[attr-defined]
                "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
                "OVRS_EXCG_CD": codes.order_excg_cd,
                "TR_CRCY_CD": "USD" if market.upper() == "US" else "",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
        balances = []
        for row in raw.get("output1", []):
            qty = Decimal(row.get("ovrs_cblc_qty", "0"))
            if qty == 0:
                continue
            balances.append(
                AccountBalance(
                    exchange="kis",
                    asset=row.get("ovrs_pdno", ""),
                    total=qty,
                    available=Decimal(row.get("ord_psbl_qty", str(qty))),
                    used_margin=Decimal("0"),
                )
            )
        return balances
