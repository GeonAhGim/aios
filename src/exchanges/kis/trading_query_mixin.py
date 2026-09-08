"""6.9-보강/L4-21 — KISAdapter 주문 **조회** 메서드군(read-only, 자금 이동 없음).

Spec: 02d_kis_api_supplement_v1.md#§2(FD-4.1/4.4/20),
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F5-b, §9 L4-21

`trading_mixin.py`(주문 변경계, place/cancel/modify)가 L4-21로 300줄 캡에
닿아 조회 전용 메서드를 이 파일로 분리했다(bitget/trading_query_mixin.py와
동일 판단, task-1519 선례). 기존 4개 메서드(get_buyable_amount 등)는
순수 이동이고, `get_open_orders`/`get_order_history`/`find_order_by_match`
가 이 리프의 신규 로직이다(§6 F5-b UNKNOWN 역조회).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.data.models.trading import Order
from src.exchanges.common.http_client import KISHTTPClient
from src.exchanges.kis.order_reverse_lookup import (
    OrderMatchQuery,
    find_matching_order,
    row_to_order,
)

_EXCHANGE_ID = "KRX"  # Phase 1 대상(06번 §6.1)
_KST_OFFSET_HOURS = 9


def _kst_today() -> str:
    """KIS는 KST 달력일 기준으로 "당일"을 해석한다 — UTC 자정 이후
    15~24시(KST로는 이미 다음 날) 구간에 UTC 날짜를 그대로 쓰면 어제
    날짜로 조회돼 오늘 주문을 놓친다(order_reverse_lookup.row_to_order의
    order_date 가정과도 맞춰야 한다: 그쪽은 이 값을 KST 달력일로 본다)."""
    return (datetime.now(timezone.utc) + timedelta(hours=_KST_OFFSET_HOURS)).strftime("%Y%m%d")


class _ReverseLookupClient(KISHTTPClient, Protocol):
    """`find_order_by_match`가 같은 클래스의 `get_open_orders`/
    `get_order_history`를 호출한다 — trading_mixin.py의 `_OrderMutatingClient`
    와 동일 이유로 명시적으로 계약에 포함한다."""

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    async def get_order_history(
        self, symbol: str | None = None, *, date: str | None = None
    ) -> list[Order]: ...


class KISTradingQueryMixin:
    async def get_buyable_amount(
        self: KISHTTPClient, symbol: str, price: Decimal
    ) -> dict[str, Any]:
        """02d 스펙 §2(P0) — FD-4.1 사전검증(주문가능금액/수량). 공식
        예제(inquire_psbl_order) 기준 최선 추정치, 라이브 검증 필요.
        raw dict 반환 — 현금/신용/증거금 등 여러 금액 필드가 함께
        내려와(§2 모델 재사용 원칙) 아직 모델화하지 않는다."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            "TTTC8908R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_UNPR": str(price),
                "ORD_DVSN": "00",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        return dict(raw.get("output", {}))

    async def get_sellable_quantity(self: KISHTTPClient, symbol: str) -> Decimal:
        """02d 스펙 §2(P0). 공식 예제(inquire_psbl_sell) 기준 최선
        추정치, 라이브 검증 필요."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-sell",
            "TTTC8408R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "PDNO": symbol,
            },
        )
        output = raw.get("output", {})
        return Decimal(output.get("ord_psbl_qty", "0"))

    async def get_cancelable_orders(self: KISHTTPClient) -> list[dict[str, Any]]:
        """02d 스펙 §2(P0) — FD-4.4 정정 전 검증. 공식 예제
        (inquire_psbl_rvsecncl) 기준 최선 추정치, 라이브 검증 필요. raw
        dict 리스트 반환(정정취소 가능수량 등 KIS 전용 필드 위주라
        Order 모델과 형태가 다름)."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            "TTTC0084R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0",
                "INQR_DVSN_2": "0",
            },
        )
        return list(raw.get("output", []))

    async def get_realized_pnl(
        self: KISHTTPClient, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]:
        """02d 스펙 §2(P0) — FD-20(운용보고서) 보강용. 공식 예제
        (inquire_balance_rlz_pl) 기준 최선 추정치, 라이브 검증 필요.
        `start_date`/`end_date`는 "YYYYMMDD", 생략 시 당일."""
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl",
            "TTTC8494R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": start_date or today,
                "INQR_END_DT": end_date or today,
                "PDNO": "",
                "CBLC_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        return list(raw.get("output1", []))

    async def get_open_orders(self: KISHTTPClient, symbol: str | None = None) -> list[Order]:
        """§6 F5-b 역조회 1단계. 별도 "미체결 목록" 전용 TR을 공식 예제
        에서 찾지 못해 inquire-psbl-rvsecncl(정정취소가능주문조회,
        get_cancelable_orders와 동일 엔드포인트)을 재사용한다(**미검증**)."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            "TTTC0084R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0",
                "INQR_DVSN_2": "0",
            },
        )
        orders = [row_to_order(row, order_date=_kst_today()) for row in raw.get("output", [])]
        return [o for o in orders if symbol is None or o.symbol == symbol]

    async def get_order_history(
        self: KISHTTPClient, symbol: str | None = None, *, date: str | None = None
    ) -> list[Order]:
        """§6 F5-b 역조회 2단계. `get_order()`가 단건 조회에 쓰는 것과
        같은 TR(inquire-daily-ccld)을 ODNO 필터 없이 호출해 지정일(생략
        시 당일, §6 F5-b "today") 전체 행을 돌려준다."""
        order_date = date or _kst_today()
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "TTTC0081R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": order_date,
                "INQR_END_DT": order_date,
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": symbol or "",
                "CCLD_DVSN": "00",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            },
        )
        orders = [
            row_to_order(row, order_date=order_date) for row in raw.get("output1", [])
        ]
        return [o for o in orders if symbol is None or o.symbol == symbol]

    async def find_order_by_match(
        self: _ReverseLookupClient, query: OrderMatchQuery
    ) -> Order | None:
        """§6 F5-b — client_order_id가 없는 KIS의 UNKNOWN 역조회.
        `find_order_by_client_id`(F5-a, Bitget 전용)와 달리 symbol/side/
        quantity/price/제출시각으로 후보를 매칭한다. 후보가 2개 이상이면
        `find_matching_order`가 `MultipleCandidateOrdersError`를 던져
        즉시 ESCALATE한다(자동 판단 금지) — 여기서 폴백으로 하나를 고르지
        않는다."""
        open_orders = await self.get_open_orders(query.symbol)
        history = await self.get_order_history(query.symbol)
        candidates: dict[str, Order] = {}
        for order in (*open_orders, *history):
            candidates.setdefault(order.exchange_order_id or "", order)
        return find_matching_order(query, list(candidates.values()))
