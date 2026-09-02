"""02d_kis_api_full_spec_v1.md §4 — KISAdapter ELW(주식워런트증권) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §4, §7(작업 분해 5번)

06번 §6.1-A 재확인 — Phase 1 확정 스콥 밖, 사용자 요청("모든 기능")에
따른 API 연동만 제공. 시세조회만이 이 문서의 최소 커버리지(ELW는
파생상품성 상품이라 매매까지는 이 리프 범위 밖). tr_id/path는 공식
예제(github.com/koreainvestment/open-trading-api/examples_llm/
domestic_stock/inquire_elw_price, 2026-09-02 WebFetch 확인) 그대로:
GET /uapi/domestic-stock/v1/quotations/inquire-elw-price (FHKEW15010000).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.market_data import Ticker

_MARKET_CODE = "W"  # ELW시장(공식 예제 확인)


class KISElwMixin:
    async def get_elw_price(self, elw_code: str) -> Ticker:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-elw-price",
            "FHKEW15010000",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": elw_code},
        )
        output = raw.get("output", {})
        price = Decimal(output.get("stck_prpr", "0"))
        return Ticker(
            symbol=elw_code,
            exchange="kis",
            price=price,
            bid=price,  # 응답에 최우선호가 없음(문서 관례) — 근사치
            ask=price,
            volume_24h=Decimal(output.get("acml_vol", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )
