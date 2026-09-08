"""02d_kis_api_full_spec_v1.md §4 — KISAdapter 해외주식(overseas_stock) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §4, §7(작업 분해 3번)
ADR-2026-09-06-I D2 — BR-4: 거래소 전수(US를 NAS/NASD 하나로 뭉치지 않는다).
ADR-2026-09-06-H D7 — L4-32: the path by which a user's own brokerage
connection participates in overseas markets such as the US.
`KISAdapter.get_capabilities()` (BR-8, adapter.py) now declares
US_EQUITY/US_ETF/US_ETN, and `place_order()` actually branches into this
mixin (order_dispatch.py), so the `ExchangeAdapter` ABC contract is already
fulfilled — what this leaf leaves behind is the last piece D7 requires:
tagging quotes that arrive through this path as `USER_SCOPED` (see
`get_overseas_ticker`). The reason US real-time quotes can be rendered on
screen without a redistribution agreement is that the connection is owned
by the user themselves, and that fact must be marked on the quote itself so
that leaking into a shared cache or screener is structurally prevented (the
D7 principle).

최초 조사(WebFetch, github.com/koreainvestment/open-trading-api/
examples_llm/overseas_stock, 2026-09-02)로 실제 예제 코드의 tr_id/
path/파라미터명을 확인했다. **핵심 함정**: 시세조회 거래소코드(EXCD,
3자리, 예: "NAS")와 주문 거래소코드(OVRS_EXCG_CD, 4자리, 예: "NASD")가
서로 다른 코드 체계다 — 하나로 통일하면 안 됨(공식 예제로 직접 확인).
그래서 이 모듈의 공개 함수는 국가 코드("US")가 아니라 **주문 코드
(4자리, 예: "NASD"/"NYSE"/"AMEX")를 단일 식별자로 받는다** — 시세조회
시 내부적으로만 대응하는 3자리 EXCD를 찾아 쓴다. 시세코드(3자리)를
그대로 넘기면 주문 거래소 표에 없으므로 명시적으로 거부된다.

매수/매도 tr_id는 국가마다 다르고, 실전 tr_id는 전부 "T"로 시작해
기존 `_resolve_tr_id()`의 T→V 치환 규칙이 그대로 적용된다(추가 매핑
불필요). NYSE/AMEX/HNX(하노이)는 각 국가의 대표 거래소(NASD/HSX)와
tr_id를 공유한다고 가정했다 — 02d_kis_api_full_spec_v1.md §4 조사에서
거래소 코드 자체는 확인했으나 국가 내 거래소별 tr_id 차이는 공식
예제로 직접 확인하지 못했다(**미검증**, 8.3 원칙: 틀리면 거래소가
오류를 반환할 뿐 잘못된 주문이 나가지는 않는다).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, NamedTuple

from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus
from src.exchanges.common.http_client import KISHTTPClient
from src.exchanges.common.live_guard import require_paper_sandbox


class _ExchangeCodes(NamedTuple):
    quote_excd: str  # 시세조회용 3자리 코드(EXCD) — 조회 전용, 조회 키 아님
    order_excg_cd: str  # 주문용 4자리 코드(OVRS_EXCG_CD) — 이 표의 조회 키
    buy_tr_id: str
    sell_tr_id: str
    currency: str


# 거래소별 코드/tr_id. 키는 주문용 4자리 코드(OVRS_EXCG_CD) — 시세조회
# 3자리 코드(EXCD)로는 조회할 수 없다(두 체계를 하나로 통일하지 않는다,
# ADR-2026-09-06-I D2). US 3거래소(NASD/NYSE/AMEX)는 공식 예제(order/
# order.py)로 NASD만 직접 확인했고, NYSE/AMEX는 같은 tr_id를 공유한다는
# 가정(미검증)이다. HK/SH/SZ/JP는 국가당 거래소가 하나라 그대로다.
# VN은 HSX(호치민)를 공식 예제로 확인, HNX(하노이)는 tr_id 공유 가정(미검증).
_EXCHANGES: dict[str, _ExchangeCodes] = {
    "NASD": _ExchangeCodes("NAS", "NASD", "TTTT1002U", "TTTT1006U", "USD"),
    "NYSE": _ExchangeCodes("NYS", "NYSE", "TTTT1002U", "TTTT1006U", "USD"),  # 미검증
    "AMEX": _ExchangeCodes("AMS", "AMEX", "TTTT1002U", "TTTT1006U", "USD"),  # 미검증
    "SEHK": _ExchangeCodes("HKS", "SEHK", "TTTS1002U", "TTTS1001U", "HKD"),
    "SHAA": _ExchangeCodes("SHS", "SHAA", "TTTS0202U", "TTTS1005U", "CNY"),
    "SZAA": _ExchangeCodes("SZS", "SZAA", "TTTS0305U", "TTTS0304U", "CNY"),
    "TKSE": _ExchangeCodes("TSE", "TKSE", "TTTS0308U", "TTTS0307U", "JPY"),
    "VNSE": _ExchangeCodes("HSX", "VNSE", "TTTS0311U", "TTTS0310U", "VND"),
    "HASE": _ExchangeCodes("HNX", "HASE", "TTTS0311U", "TTTS0310U", "VND"),  # 미검증
}


def _exchange_codes(exchange: str) -> _ExchangeCodes:
    codes = _EXCHANGES.get(exchange.upper())
    if codes is None:
        raise ValueError(
            f"지원하지 않는 해외주식 거래소입니다: {exchange!r} "
            f"(지원: {', '.join(_EXCHANGES)}) — 시세조회용 3자리 코드(EXCD, "
            "예: NAS)가 아니라 주문용 4자리 코드(OVRS_EXCG_CD, 예: NASD)를 "
            "써야 합니다."
        )
    return codes


class KISOverseasStockMixin:
    async def get_overseas_ticker(self: KISHTTPClient, symbol: str, exchange: str) -> Ticker:
        codes = _exchange_codes(exchange)
        raw = await self._request(
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
            redistribution_scope="USER_SCOPED",
        )

    @require_paper_sandbox
    async def place_overseas_order(self: KISHTTPClient, order: Order, exchange: str) -> Order:
        codes = _exchange_codes(exchange)
        tr_id = codes.buy_tr_id if order.side == OrderSide.BUY else codes.sell_tr_id
        body: dict[str, Any] = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
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
        raw = await self._request(
            "POST", "/uapi/overseas-stock/v1/trading/order", tr_id, body=body
        )
        output = raw.get("output", {})
        exchange_order_id = f"{output.get('KRX_FWDG_ORD_ORGNO', '')}:{output.get('ODNO', '')}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_overseas_order(
        self: KISHTTPClient,
        order_id: str,
        symbol: str,
        exchange: str,
        *,
        original_quantity: Decimal,
    ) -> bool:
        """미국 거래소 기준으로 확인된 tr_id(TTTT1004U)만 신뢰도가 있다 —
        다른 거래소의 정정취소 tr_id는 이번 조사에서 확인하지 못해 US와
        동일 tr_id를 임시로 재사용한다(라이브 검증 전까지 확정 아님,
        틀렸다면 거래소가 오류를 반환할 뿐 잘못된 주문이 나가지는
        않는다 — 8.3 원칙)."""
        codes = _exchange_codes(exchange)
        orgno, odno = order_id.split(":", 1)
        body: dict[str, Any] = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "OVRS_EXCG_CD": codes.order_excg_cd,
            "PDNO": symbol,
            "ORGN_ODNO": odno,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(original_quantity),
            "OVRS_ORD_UNPR": "0",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0",
        }
        raw = await self._request(
            "POST", "/uapi/overseas-stock/v1/trading/order-rvsecncl", "TTTT1004U", body=body
        )
        return bool(raw.get("rt_cd") == "0")

    async def get_overseas_balance(
        self: KISHTTPClient, exchange: str
    ) -> list[AccountBalance]:
        codes = _exchange_codes(exchange)
        raw = await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            "TTTS3012R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "OVRS_EXCG_CD": codes.order_excg_cd,
                "TR_CRCY_CD": codes.currency,
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
