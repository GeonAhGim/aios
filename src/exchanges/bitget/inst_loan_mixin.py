"""02c_bitget_api_v2_extended_spec_v1.md §1.11 — BitgetAdapter Inst Loan(기관 전용 대출) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.11, §2(작업 분해 11번)

기관 계정 전용 — 리테일 API 키로는 대부분 권한 오류가 날 가능성이
높지만, API 연동 자체는 제공한다(8.3 원칙: 권한 없음도 정상적인 응답
케이스로 처리, 클라이언트가 선제적으로 막지 않는다). `loan_mixin.py`의
코인담보대출(리테일)과 별개 네임스페이스(`/api/v2/ins-loan/*`). 상환
실행 엔드포인트는 공식 문서로 확인되지 않아 조회 전용으로만 우선
제공한다(모듈 docstring 참조, 확인되지 않은 엔드포인트를 추측으로
만들지 않는다는 원칙). 엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브
검증 필요):
- GET /api/v2/ins-loan/product-infos
- GET /api/v2/ins-loan/ensure-coins-convert
- GET /api/v2/ins-loan/loan-order
- GET /api/v2/ins-loan/repaid-history
"""
from __future__ import annotations

from typing import Any


class BitgetInstLoanMixin:
    async def get_inst_loan_products(self) -> list[dict[str, Any]]:
        raw = await self._request("GET", "/api/v2/ins-loan/product-infos")  # type: ignore[attr-defined]
        return list(raw["data"])

    async def get_inst_loan_ensure_coins(self) -> list[dict[str, Any]]:
        """담보 코인 목록 및 환산율 조회."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/ins-loan/ensure-coins-convert"
        )
        return list(raw["data"])

    async def get_inst_loan_orders(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """진행중 대출(LTV 포함) 조회."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/ins-loan/loan-order", params={"limit": str(limit)}
        )
        return list(raw["data"])

    async def get_inst_loan_repaid_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/ins-loan/repaid-history", params={"limit": str(limit)}
        )
        return list(raw["data"])
