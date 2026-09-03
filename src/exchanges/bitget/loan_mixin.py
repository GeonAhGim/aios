"""02c_bitget_api_v2_extended_spec_v1.md §1.5 — BitgetAdapter Loan(코인담보대출) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.5, §2(작업 분해 5번)

Margin의 "마진 대출"(거래용 신용)과 달리, 담보를 맡기고 다른 코인을
빌리는 별도 신용상품(거래 목적 아님). `ExchangeAdapter` ABC에는 아직
없음(다른 확장 메서드들과 동일 원칙). 엔드포인트(커뮤니티 SDK
레퍼런스 기준, 라이브 검증 필요):
- GET  /api/v2/loan/coin-info
- GET  /api/v2/loan/hourly-interest-rate
- POST /api/v2/loan/borrow
- POST /api/v2/loan/repay
- POST /api/v2/loan/revise-pledge
- GET  /api/v2/loan/ongoing-orders
- GET  /api/v2/loan/repay-history
- GET  /api/v2/loan/liquidation-records
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.exchanges.common.http_client import SignedRequestClient
from src.exchanges.common.live_guard import require_paper_sandbox


class BitgetLoanMixin:
    async def get_loan_coin_info(
        self: SignedRequestClient,
        *,
        coin: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(
            "GET", "/api/v2/loan/coin-info", params=params or None
        )
        return list(raw["data"])

    async def get_loan_hourly_interest_rate(
        self: SignedRequestClient,
        coin: str,
    ) -> list[dict[str, Any]]:
        raw = await self._request(
            "GET", "/api/v2/loan/hourly-interest-rate", params={"coin": coin.upper()}
        )
        return list(raw["data"])

    @require_paper_sandbox
    async def borrow_loan(
        self: SignedRequestClient, loan_coin: str, pledge_coin: str, pledge_amount: Decimal
    ) -> dict[str, Any]:
        """담보(pledge)를 맡기고 다른 코인을 빌린다 — margin_mixin.py의
        거래용 대출(borrow_margin)과 다른 상품(§1.5 모듈 docstring).
        레드팀 #2026-09-02-32/33 — 이전엔 이 경고 docstring도, LIVE
        adapter 차단도, 금액 검증도 없었다(borrow_margin에만 경고가
        있었고 이건 누락돼 있었음). 이 호출은 FD-8.3 RiskEngine 승인
        이후에만 트리거되어야 한다 — 이 메서드 자체는 그 정책 게이트를
        강제하지 않는다(API 연동 레이어, 정책 적용은 호출부 책임)."""
        if pledge_amount <= 0:
            raise ValueError("pledge_amount는 0보다 커야 합니다.")
        raw = await self._request(
            "POST",
            "/api/v2/loan/borrow",
            body={
                "loanCoin": loan_coin.upper(),
                "pledgeCoin": pledge_coin.upper(),
                "pledgeAmount": str(pledge_amount),
            },
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def repay_loan(
        self: SignedRequestClient,
        order_id: str,
        amount: Decimal,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount는 0보다 커야 합니다.")
        raw = await self._request(
            "POST", "/api/v2/loan/repay", body={"orderId": order_id, "amount": str(amount)}
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def revise_loan_pledge(
        self: SignedRequestClient, order_id: str, amount: Decimal, *, reverse_type: str = "IN"
    ) -> dict[str, Any]:
        """담보 추가(`reverse_type="IN"`)/감액(`"OUT"`, 문서 관례).
        레드팀 #2026-09-02-32/33 참조."""
        if amount <= 0:
            raise ValueError("amount는 0보다 커야 합니다.")
        raw = await self._request(
            "POST",
            "/api/v2/loan/revise-pledge",
            body={"orderId": order_id, "amount": str(amount), "reviseType": reverse_type},
        )
        return dict(raw["data"])

    async def get_ongoing_loans(
        self: SignedRequestClient,
        *,
        order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if order_id is not None:
            params["orderId"] = order_id
        raw = await self._request(
            "GET", "/api/v2/loan/ongoing-orders", params=params or None
        )
        return list(raw["data"])

    async def get_loan_repay_history(
        self: SignedRequestClient, *, order_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if order_id is not None:
            params["orderId"] = order_id
        raw = await self._request(
            "GET", "/api/v2/loan/repay-history", params=params
        )
        return list(raw["data"])

    async def get_loan_liquidation_records(
        self: SignedRequestClient, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """FD-9.6(Reconciliation) 입력값 후보(margin_mixin.py의
        get_margin_liquidation_orders와 동일 목적, 별도 신용상품이라
        별도 엔드포인트)."""
        raw = await self._request(
            "GET", "/api/v2/loan/liquidation-records", params={"limit": str(limit)}
        )
        return list(raw["data"])
