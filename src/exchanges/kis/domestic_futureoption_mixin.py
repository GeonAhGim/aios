"""02d_kis_api_full_spec_v1.md §5 — KISAdapter 국내선물옵션(domestic_futureoption) 메서드군.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §10 BR,
ADR-2026-09-06-I D2/Rejected("선물옵션을 계속 P2로 두기") — BR-6:
국내선물옵션은 P2("Phase 1 스콥 밖, 당장 안 씀")에서 P1로 승격됐다.
02d §5 본문은 아직 옛 P2 문구 그대로지만 이 ADR이 그 문구를 무효화한다
(02d §5 자체 갱신은 이 태스크 files 목록 밖 — 문서 후속 리프).

06번 §6.1-A 재확인 — Phase 1 확정 스콥은 KR_EQUITY뿐이라 이 mixin도
domestic_bond/overseas_stock과 같은 원칙으로 `ExchangeAdapter` ABC 밖의
KIS 전용 확장이다.

tr_id는 `docs/design/kis_tr_reference.json`(공식 저장소 examples_llm/
domestic_futureoption/* 기계 추출 스냅샷, KIS_TR_COVERAGE.md 참조)에서
가져왔다: 주문 TTTO1101U(정규장), 정정취소 TTTO1103U, 잔고현황
CTFO6118R, 시세 FHMIF10000000 — 전부 실전 tr_id이고 모의투자 치환은
어댑터의 기존 T/J/C→V 규칙(adapter.py `_resolve_tr_id`)이 그대로
처리한다(CTFO6118R → VTFO6118R은 KIS_TR_COVERAGE.md에 별도 행으로도
확인됨). 다만 스냅샷은 tr_id/제목/예제 경로만 담고 실제 요청 본문은
포함하지 않으므로, 파라미터명·시장구분코드는 국내주식/ETF 명명 관례
(FID_COND_MRKT_DIV_CODE, PDNO, ORD_QTY, ORD_UNPR류)를 그대로 연장한
**미검증** 최선 추정치다(8.3 원칙 — 틀리면 거래소가 오류를 반환할 뿐
잘못된 주문이 나가지는 않는다). 라이브 검증 전까지 확정 아님.

만기 종목 주문 거부(BR-6 DoD)는 거래소 응답에 기대지 않는다 — 거래소가
만기 지난 종목의 주문을 실제로 어떻게 처리하는지 이번 조사로 확인하지
못했으므로(같은 미검증 사유), `Order.expiry_date`를 이 mixin이 요청을
보내기 전에 직접 검사해 fail-closed로 거부한다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.live_guard import require_paper_sandbox

_MARKET_CODE_FUTURES = "JF"  # 국내지수선물 등 선물류(미검증 — §4 시장코드 명명 관례 연장)
_MARKET_CODE_OPTION = "JO"  # 국내지수옵션 등 옵션류(미검증)

_DERIVATIVE_ASSET_CLASSES = (AssetClass.KR_FUTURES, AssetClass.KR_OPTION)
_KST = ZoneInfo("Asia/Seoul")  # KRX 거래일 기준 — foundation/market_data known_venues.py와 동일


class ContractExpiredError(ValueError):
    """만기가 지난 선물옵션 종목에 대한 주문 요청 — BR-6 DoD "만기 종목 주문 거부"."""


def _require_derivative_asset_class(order: Order) -> None:
    """ADR-2026-08-28 다자산군 확장 원칙(01번 §1.0) — asset_class가 필수인
    이유가 "침묵 오분류 방지"이므로, 이 mixin이 실제로 다루는 자산군이
    아니면 명시적으로 거부한다(호출부가 실수로 KR_EQUITY 주문을 이
    엔드포인트로 보내는 사고 방지)."""
    if order.asset_class not in _DERIVATIVE_ASSET_CLASSES:
        raise ValueError(
            f"국내선물옵션 주문은 asset_class가 {_DERIVATIVE_ASSET_CLASSES} 중 하나여야 "
            f"합니다: {order.asset_class!r}"
        )


def _reject_if_expired(expiry_date: date | None, *, now: datetime | None = None) -> None:
    """`expiry_date`는 KRX 계약 만기일(달력일, 타임존 없음)이므로 "오늘"도
    서버 로컬/UTC가 아니라 거래소 로컬시간(KST)로 판단한다 — UTC로 비교하면
    한국 자정 전후 몇 시간 동안 하루 오차가 생긴다."""
    if expiry_date is None:
        return
    today = (now or datetime.now(timezone.utc)).astimezone(_KST).date()
    if expiry_date < today:
        raise ContractExpiredError(
            f"만기 지난 선물옵션 종목은 주문할 수 없습니다: "
            f"expiry_date={expiry_date}, today(KST)={today}"
        )


def _order_division(order_type: OrderType) -> str:
    """ORD_DVSN_CD 값 추정 — `trading_mixin._order_division`(국내주식 ORD_DVSN)과
    동일한 00=지정가/01=시장가 관례를 그대로 연장한다(모듈 docstring §미검증
    원칙). `kis_tr_reference.json`은 tr_id별 필수 파라미터명만 확인해 주고
    실제 코드값은 담지 않으므로, 이 값도 라이브 검증 전까지 확정 아니다."""
    return "01" if order_type == OrderType.MARKET else "00"


def calculate_settlement_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    contract_multiplier: Decimal,
    side: OrderSide,
) -> Decimal:
    """승수 반영 손익(BR-6 DoD) — (청산가-진입가) × 수량 × 승수, 매도
    포지션은 부호 반전. `foundation/positions/domain/pnl.py`의 미실현
    PnL 공식(01번 §1.4 Money 부착)과 같은 대수식이지만, 이 함수는
    Money/기준통화 환산 없이 KIS 선물옵션 승수 계산 그 자체만 순수하게
    검증한다 — 통화 부착은 호출부(포지션 평가 계층)의 책임이다."""
    direction = Decimal("1") if side == OrderSide.BUY else Decimal("-1")
    return (exit_price - entry_price) * quantity * contract_multiplier * direction


class KISDomesticFutureoptionMixin:
    async def get_futureoption_price(self, symbol: str, *, is_option: bool = False) -> Ticker:
        market_code = _MARKET_CODE_OPTION if is_option else _MARKET_CODE_FUTURES
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-futureoption/v1/quotations/inquire-price",
            "FHMIF10000000",
            params={"FID_COND_MRKT_DIV_CODE": market_code, "FID_INPUT_ISCD": symbol},
        )
        output = raw.get("output", {})
        price = Decimal(output.get("futs_prpr", output.get("stck_prpr", "0")))
        return Ticker(
            symbol=symbol,
            exchange="kis",
            price=price,
            bid=price,  # 응답에 최우선호가 없음(문서 관례) — 근사치
            ask=price,
            volume_24h=Decimal(output.get("acml_vol", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )

    @require_paper_sandbox
    async def place_futureoption_order(self, order: Order) -> Order:
        _require_derivative_asset_class(order)
        _reject_if_expired(order.expiry_date)
        body: dict[str, Any] = {
            "ORD_PRCS_DVSN_CD": "02",
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "SLL_BUY_DVSN_CD": "02" if order.side == OrderSide.BUY else "01",
            "SHTN_PDNO": order.symbol,
            "ORD_QTY": str(order.quantity),
            "UNIT_PRICE": str(order.price.amount) if order.price is not None else "0",
            "NMPR_TYPE_CD": "01",
            "KRX_NMPR_CNDT_CD": "0",
            "ORD_DVSN_CD": _order_division(order.order_type),
            "CTAC_TLNO": "",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/uapi/domestic-futureoption/v1/trading/order", "TTTO1101U", body=body
        )
        try:
            output = raw["output"]
            exchange_order_id = f"{output['KRX_FWDG_ORD_ORGNO']}:{output['ODNO']}"
        except KeyError as exc:
            raise FatalExchangeError(f"KIS 선물옵션 주문 응답에 예상 필드 없음: {exc}") from exc
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_futureoption_order(self, order_id: str, *, quantity: Decimal) -> bool:
        orgno, odno = order_id.split(":", 1)
        body: dict[str, Any] = {
            "ORD_PRCS_DVSN_CD": "02",
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "ORGN_ODNO": odno,
            "KRX_FWDG_ORD_ORGNO": orgno,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(quantity),
            "UNIT_PRICE": "0",
            "NMPR_TYPE_CD": "01",
            "KRX_NMPR_CNDT_CD": "0",
            # "N" — 이 메서드 시그니처는 quantity를 필수로 받아 항상 명시
            # 수량으로 취소하므로("잔량 전부" 자동취소가 아님), 국내주식
            # QTY_ALL_ORD_YN 관례(trading_mixin._rvsecncl)와 같은 판단 기준을
            # 그대로 적용한 미검증 최선 추정치다.
            "RMN_QTY_YN": "N",
            "ORD_DVSN_CD": "02",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/uapi/domestic-futureoption/v1/trading/order-rvsecncl",
            "TTTO1103U",
            body=body,
        )
        return bool(raw.get("rt_cd") == "0")

    async def get_futureoption_balance(self) -> list[AccountBalance]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-futureoption/v1/trading/inquire-balance",
            "CTFO6118R",
            params={
                "CANO": self._cano,  # type: ignore[attr-defined]
                "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
                "MGNA_DVSN": "01",
                "EXCC_STAT_CD": "1",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
        balances = []
        for row in raw.get("output1", []):
            qty = Decimal(row.get("cblc_qty", "0"))
            if qty == 0:
                continue
            balances.append(
                AccountBalance(
                    exchange="kis",
                    asset=row.get("pdno", ""),
                    total=qty,
                    available=Decimal(row.get("ord_psbl_qty", str(qty))),
                    used_margin=Decimal(row.get("mntn_mgn", "0")),
                )
            )
        return balances
