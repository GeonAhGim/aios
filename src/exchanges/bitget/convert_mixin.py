"""02c_bitget_api_v2_extended_spec_v1.md §1.1 — BitgetAdapter Convert(간편환전) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.1, §2(작업 분해 1번)

`ExchangeAdapter` ABC에는 아직 없는 Bitget 전용 확장 메서드다(다른
확장 메서드들과 동일 원칙 — 소비하는 FD-4/8 호출부가 생기기 전까지
ABC로 승격하지 않음). 엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브
검증 필요):
- GET  /api/v2/convert/currencies
- GET  /api/v2/convert/quoted-price
- POST /api/v2/convert/trade
- GET  /api/v2/convert/convert-record
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.exchanges.common.live_guard import require_paper_sandbox


class BitgetConvertMixin:
    async def get_convert_currencies(self) -> list[dict[str, Any]]:
        """지원 코인쌍 목록 — raw dict 반환(§2 모델 재사용 원칙, 소비하는
        호출부가 생기기 전까지 모델화 보류)."""
        raw = await self._request("GET", "/api/v2/convert/currencies")  # type: ignore[attr-defined]
        return list(raw["data"])

    async def get_convert_quote(
        self, from_coin: str, to_coin: str, from_amount: Decimal
    ) -> dict[str, Any]:
        """환전 실행 전 필수 — 응답의 `traceId`를 `execute_convert()`에
        그대로 전달해야 한다(견적-실행 2단계 흐름, 문서 관례)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/convert/quoted-price",
            params={
                "fromCoin": from_coin.upper(),
                "toCoin": to_coin.upper(),
                "fromCoinSize": str(from_amount),
            },
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def execute_convert(
        self,
        trace_id: str,
        from_coin: str,
        to_coin: str,
        from_amount: Decimal,
        to_amount: Decimal,
    ) -> dict[str, Any]:
        """`get_convert_quote()`가 반환한 `traceId`/금액을 그대로 재전달해야
        한다(문서 관례 — 견적과 다른 금액을 보내면 거래소가 거부).

        레드팀 #2026-09-02-32/33 — Executor를 거치지 않으므로 최소한의
        방어선(LIVE adapter 차단 + 금액 sanity check)을 이 메서드 자체에
        건다."""
        if from_amount <= 0 or to_amount <= 0:
            raise ValueError("from_amount/to_amount는 0보다 커야 합니다.")
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/convert/trade",
            body={
                "traceId": trace_id,
                "fromCoin": from_coin.upper(),
                "toCoin": to_coin.upper(),
                "fromCoinSize": str(from_amount),
                "toCoinSize": str(to_amount),
            },
        )
        return dict(raw["data"])

    async def get_convert_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/convert/convert-record", params={"limit": str(limit)}
        )
        return list(raw["data"])
