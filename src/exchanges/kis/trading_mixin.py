"""6.9/6.10 — KISAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트(2026-08-28 KIS 공식 GitHub 예제 소스코드 확인):
- POST /uapi/domestic-stock/v1/trading/order-cash, tr_id TTTC0012U(매수)/
  TTTC0011U(매도) — 모의투자 치환은 어댑터가 자동 처리
- POST /uapi/domestic-stock/v1/trading/order-rvsecncl (정정·취소 통합),
  RVSE_CNCL_DVSN_CD로 구분("01"=정정, "02"=취소)
- GET  /uapi/domestic-stock/v1/trading/inquire-daily-ccld, tr_id TTTC0081R
  (최근 3개월 이내 체결조회)

편차: KIS는 주문 취소/정정 시 KRX_FWDG_ORD_ORGNO(거래소전송주문조직번호)와
ORGN_ODNO(원주문번호)가 모두 필요하지만 Order.exchange_order_id는 단일
문자열이다 — place_order()가 "{orgno}:{odno}" 형식으로 합쳐 저장하고,
cancel_order/modify_order가 그 형식을 기대한다(문서화된 편의 규약).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.http_client import KISHTTPClient

_EXCHANGE_ID = "KRX"  # Phase 1 대상(06번 §6.1)


def _order_division(order_type: OrderType) -> str:
    return "01" if order_type == OrderType.MARKET else "00"


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"KIS exchange_order_id는 'orgno:odno' 형식이어야 함: {exchange_order_id}"
        )
    orgno, odno = exchange_order_id.split(":", 1)
    return orgno, odno


class _BalanceCheckingClient(KISHTTPClient, Protocol):
    """health_check()가 같은 어댑터에 조립되는 KISAccountMixin.get_balance()를
    호출하지만, self가 KISHTTPClient로 좁혀진 메서드 안에서는 그 사실이
    보이지 않으므로 명시적으로 계약에 포함한다(bitget _OrderReadingClient와
    동일 패턴)."""

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...


class _OrderMutatingClient(KISHTTPClient, Protocol):
    """cancel_order()/modify_order()가 같은 클래스의 _rvsecncl()/get_order()를
    호출한다 — 위와 동일 이유로 명시적으로 계약에 포함한다."""

    async def _rvsecncl(
        self, order_id: str, *, decision: str, quantity: Decimal | None
    ) -> dict[str, Any]: ...

    async def get_order(self, order_id: str) -> Order: ...


class KISTradingMixin:
    async def place_order(self: KISHTTPClient, order: Order) -> Order:
        body: dict[str, Any] = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": order.symbol,
            "ORD_DVSN": _order_division(order.order_type),
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(order.price.amount) if order.price is not None else "0",
            "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            "SLL_TYPE": "01" if order.side == OrderSide.SELL else "",
            "CNDT_PRIC": "",
        }
        tr_id = "TTTC0012U" if order.side == OrderSide.BUY else "TTTC0011U"
        raw = await self._request(
            "POST", "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body=body
        )
        # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #18b) 반영 — market_data_mixin과
        # 동일하게 예상 필드 누락을 FatalExchangeError로 통일한다(설명 없는
        # KeyError 대신 어떤 필드가 없었는지 드러낸다).
        try:
            output = raw["output"]
            exchange_order_id = f"{output['KRX_FWDG_ORD_ORGNO']}:{output['ODNO']}"
        except KeyError as exc:
            raise FatalExchangeError(f"KIS 주문 응답에 예상 필드 없음: {exc}") from exc
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    async def _rvsecncl(
        self: KISHTTPClient, order_id: str, *, decision: str, quantity: Decimal | None
    ) -> dict[str, Any]:
        orgno, odno = _split_exchange_order_id(order_id)
        body: dict[str, Any] = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": orgno,
            "ORGN_ODNO": odno,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": decision,
            "ORD_QTY": str(quantity) if quantity is not None else "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "N" if quantity is not None else "Y",
            "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
        }
        return await self._request(
            "POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", "TTTC0013U", body=body
        )

    async def cancel_order(self: _OrderMutatingClient, order_id: str) -> bool:
        raw = await self._rvsecncl(order_id, decision="02", quantity=None)
        return bool(raw.get("rt_cd") == "0")

    async def modify_order(self: _OrderMutatingClient, order_id: str, **kwargs: Any) -> Order:
        quantity = kwargs.get("quantity")
        await self._rvsecncl(order_id, decision="01", quantity=quantity)
        return await self.get_order(order_id)

    async def get_order(self: KISHTTPClient, order_id: str) -> Order:
        """편차: BitgetAdapter.get_order()와 동일 이유로 AIOS 전용 필드
        (strategy_id 등)는 자리표시자 — 호출부가 DB 행과 병합해야 한다."""
        _, odno = _split_exchange_order_id(order_id)
        today = datetime.now(timezone.utc)
        start = (today - timedelta(days=7)).strftime("%Y%m%d")
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "TTTC0081R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": start,
                "INQR_END_DT": today.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": odno,
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            },
        )
        rows = raw.get("output1", [])
        if not rows:
            raise FatalExchangeError(f"KIS 주문을 찾을 수 없음: order_id={order_id}")
        row = rows[0]

        filled_qty = Decimal(row.get("tot_ccld_qty", "0"))
        ord_qty = Decimal(row.get("ord_qty", "0"))
        if filled_qty == 0:
            status = OrderStatus.ACKNOWLEDGED
        elif filled_qty < ord_qty:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.FILLED

        return Order(
            order_id=uuid4(),
            exchange_order_id=order_id,
            client_order_id="",  # KIS는 client_order_id 개념이 없음(어댑터 docstring 참조)
            strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
            strategy_version="",
            symbol=row.get("pdno", ""),
            exchange="kis",
            side=OrderSide.BUY if row.get("sll_buy_dvsn_cd") == "02" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=ord_qty,
            status=status,
            filled_quantity=filled_qty,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            asset_class=AssetClass.KR_EQUITY,
        )

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

    async def health_check(self: _BalanceCheckingClient) -> bool:
        try:
            await self.get_balance()
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
