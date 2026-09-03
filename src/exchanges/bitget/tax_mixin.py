"""02c_bitget_api_v2_extended_spec_v1.md §1.6 — BitgetAdapter Tax(세금 신고용 원본 데이터) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.6, §2(작업 분해 3번)

FD-20(운용보고서) 보강용 — `account_mixin.py::get_account_bills()`보다
세무 목적에 특화된 필드를 준다(문서 관례, 라이브 검증 필요). 아직
소비하는 호출부가 없어 raw dict를 그대로 반환한다(§2 모델 재사용 원칙).
엔드포인트(커뮤니티 SDK 레퍼런스 기준):
- GET /api/v2/tax/spot-record
- GET /api/v2/tax/future-record
- GET /api/v2/tax/margin-record
- GET /api/v2/tax/p2p-record
"""
from __future__ import annotations

from typing import Any

from src.exchanges.common.http_client import SignedRequestClient


class BitgetTaxMixin:
    async def get_spot_tax_records(
        self: SignedRequestClient,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request(
            "GET", "/api/v2/tax/spot-record", params=params
        )
        return list(raw["data"])

    async def get_futures_tax_records(
        self: SignedRequestClient,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request(
            "GET", "/api/v2/tax/future-record", params=params
        )
        return list(raw["data"])

    async def get_margin_tax_records(
        self: SignedRequestClient,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request(
            "GET", "/api/v2/tax/margin-record", params=params
        )
        return list(raw["data"])

    async def get_p2p_tax_records(
        self: SignedRequestClient,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request(
            "GET", "/api/v2/tax/p2p-record", params=params
        )
        return list(raw["data"])
