"""02d_kis_api_full_spec_v1.md §5 — KISAdapter 해외선물옵션(overseas_futureoption)
메서드군.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §10 BR,
ADR-2026-09-06-I D2 — BR-7: 국내선물옵션(BR-6)에 이어 해외선물옵션(전무,
0/35)을 시세·주문·취소·잔고 수준으로 연다.

**모의투자 지원 여부(D3) — 구조적 증거로 미지원 추정**: 이 mixin이 쓰는
TR(OTFM3001U 주문/OTFM3003U 취소/OTFM1412R 잔고/HHDFC55010000·HHDFO55010000
시세)은 전부 `kis_tr_reference.json`의 overseas_futureoption 35개 TR
전체와 마찬가지로 **T/J/C로 시작하지 않는다**(O 또는 H 접두) — adapter.py의
기존 모의투자 치환 규칙(`_resolve_tr_id`, T/J/C→V)이 애초에 적용될 수
없는 접두다. 게다가 기준 목록 35개 전체를 뒤져도 V-접두 대응 TR이
단 하나도 없다(국내선물옵션 BR-6은 CTFO6118R↔VTFO6118R처럼 쌍이
확인됐던 것과 대조적). 이는 KIS 모의투자가 해외파생상품 자체를 아예
취급하지 않는다는 강한 정황 증거이지만, 공식 문서로 직접 확인하지는
못했다(**미검증**). 따라서:
  - `require_paper_sandbox`는 그대로 배선한다(ADR D3 LIVE 하드가드
    유지 — is_paper_trading=False인 adapter에서는 이 메서드들이 아예
    실행되지 않는다).
  - 다만 PAPER로 설정한 adapter로 호출해도 실제 KIS 모의투자 서버가
    이 tr_id들을 인식/체결까지 처리해줄지는 이번 리프가 검증하지
    못했다 — 이 파일의 단위테스트는 mock transport로 "요청 조립·응답
    파싱" 코드 경로만 확인한다(BR-6과 동일 한계, KIS 서버 자체는
    검증 대상이 아님). **실계좌 확보 후 실제 KIS 모의/실전 서버로
    왕복을 확인하는 검증 리프를 별도로 남긴다**(ADR D3, BR-1 매트릭스
    갱신은 `scripts/kis_tr_coverage.py`가 소스 스캔으로 자동 수행하며,
    이 리프가 구현한 TR은 전부 "구현됨"으로 등재된다 — "구현됨"은
    코드 존재를 뜻할 뿐 모의투자 왕복 검증 완료를 뜻하지 않는다).

**파라미터 명명 미검증(8.3 원칙)**: `02d_kis_api_full_spec_v1.md` §5는
"P2, 별도 확장 시 조사"로만 적혀 있고 실제 요청 본문을 담은 공식 예제를
이 세션이 확인하지 못했다. 시세 응답 필드는 국내선물옵션(futs_prpr류)이
아니라 같은 "해외" API 계열인 overseas_stock_mixin(HHDFS00000300,
last/tvol)의 명명 관례를 연장했다 — 해외파생이 해외주식과 같은 "해외"
API 패밀리 안에 있을 가능성이 국내파생과의 유사성보다 높다고 판단했으나
확정 아니다. 주문 본문 필드명(PDNO/ORD_QTY/ORD_PRC류)도 마찬가지로
기존 관례를 연장한 최선 추정치다 — 틀리면 거래소가 오류를 반환할 뿐
잘못된 주문이 나가지는 않는다(8.3 원칙).

만기 종목 주문 거부(BR-6과 동일 원칙 연장)는 거래소 응답에 기대지
않고 `Order.expiry_date`를 요청 전에 직접 검사한다. 해외파생은 계약이
여러 국가 거래소(CME/EUREX/SGX 등)에 걸쳐 있어 국내선물옵션처럼 단일
거래소 로컬시간(KST)을 기준으로 삼을 근거가 없다 — 이 mixin은 UTC
달력일을 기준으로 판단한다(특정 거래소 로컬시간보다 이르거나 늦을 수
있는 근사치, **미검증**). fail-closed 원칙상 애매하면 거부가 허용보다
안전하다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus
from src.exchanges.common.live_guard import require_paper_sandbox

_OVERSEAS_DERIVATIVE_ASSET_CLASSES = (AssetClass.OVERSEAS_FUTURES, AssetClass.OVERSEAS_OPTION)


class OverseasContractExpiredError(ValueError):
    """만기가 지난 해외선물옵션 종목에 대한 주문 요청."""


def _require_overseas_derivative_asset_class(order: Order) -> None:
    """ADR-2026-08-28 다자산군 확장 원칙(01번 §1.0) — asset_class가 필수인
    이유가 "침묵 오분류 방지"이므로, 이 mixin이 실제로 다루는 자산군이
    아니면 명시적으로 거부한다."""
    if order.asset_class not in _OVERSEAS_DERIVATIVE_ASSET_CLASSES:
        raise ValueError(
            f"해외선물옵션 주문은 asset_class가 {_OVERSEAS_DERIVATIVE_ASSET_CLASSES} 중 "
            f"하나여야 합니다: {order.asset_class!r}"
        )


def _reject_if_expired(expiry_date: date | None, *, now: datetime | None = None) -> None:
    """해외파생은 계약별 거래소 로컬시간이 제각각이라 UTC 달력일을 근사
    기준으로 쓴다(미검증 — docstring 상단 참조)."""
    if expiry_date is None:
        return
    today = (now or datetime.now(timezone.utc)).date()
    if expiry_date < today:
        raise OverseasContractExpiredError(
            f"만기 지난 해외선물옵션 종목은 주문할 수 없습니다: "
            f"expiry_date={expiry_date}, today(UTC)={today}"
        )


class KISOverseasFutureoptionMixin:
    async def get_overseas_futureoption_price(
        self, symbol: str, *, is_option: bool = False
    ) -> Ticker:
        tr_id = "HHDFO55010000" if is_option else "HHDFC55010000"
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/overseas-futureoption/v1/quotations/inquire-price",
            tr_id,
            params={"SYMB": symbol},
        )
        output = raw.get("output", {})
        price = Decimal(output.get("last", "0"))
        return Ticker(
            symbol=symbol,
            exchange="kis",
            price=price,
            bid=price,  # 응답에 최우선호가 없음(overseas_stock 관례 연장) — 근사치
            ask=price,
            volume_24h=Decimal(output.get("tvol", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )

    @require_paper_sandbox
    async def place_overseas_futureoption_order(self, order: Order) -> Order:
        _require_overseas_derivative_asset_class(order)
        _reject_if_expired(order.expiry_date)
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "SLL_BUY_DVSN_CD": "02" if order.side == OrderSide.BUY else "01",
            "PDNO": order.symbol,
            "ORD_QTY": str(order.quantity),
            "ORD_PRC": str(order.price.amount) if order.price is not None else "0",
            "ORD_DVSN": "02" if order.price is not None else "01",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/uapi/overseas-futureoption/v1/trading/order", "OTFM3001U", body=body
        )
        try:
            output = raw["output"]
            exchange_order_id = f"{output['KRX_FWDG_ORD_ORGNO']}:{output['ODNO']}"
        except KeyError as exc:
            raise FatalExchangeError(f"KIS 해외선물옵션 주문 응답에 예상 필드 없음: {exc}") from exc
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_overseas_futureoption_order(
        self, order_id: str, *, quantity: Decimal
    ) -> bool:
        orgno, odno = order_id.split(":", 1)
        body: dict[str, Any] = {
            "CANO": self._cano,  # type: ignore[attr-defined]
            "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
            "ORGN_ODNO": odno,
            "KRX_FWDG_ORD_ORGNO": orgno,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(quantity),
            "ORD_PRC": "0",
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/uapi/overseas-futureoption/v1/trading/order-rvsecncl",
            "OTFM3003U",
            body=body,
        )
        return bool(raw.get("rt_cd") == "0")

    async def get_overseas_futureoption_balance(self) -> list[AccountBalance]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/overseas-futureoption/v1/trading/inquire-balance",
            "OTFM1412R",
            params={
                "CANO": self._cano,  # type: ignore[attr-defined]
                "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
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
