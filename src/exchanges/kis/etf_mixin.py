"""02d_kis_api_full_spec_v1.md §4 — KISAdapter ETF/ETN 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §4, §7(작업 분해 5번)

06번 §6.1-A 재확인 — Phase 1 확정 스콥 밖, 사용자 요청("모든 기능")에
따른 API 연동만 제공. 시세조회만이 이 문서의 최소 커버리지(매매는
domestic_stock의 order-cash를 그대로 쓸 수 있어 — ETF/ETN도 결국
KRX에 상장된 종목코드 매매라 별도 주문 엔드포인트가 없음, 공식 예제
확인). tr_id/path는 공식 예제(github.com/koreainvestment/open-trading-
api/examples_llm/etfetn/inquire_price, 2026-09-02 WebFetch 확인) 그대로:
GET /uapi/etfetn/v1/quotations/inquire-price (FHPST02400000).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.market_data import Ticker

_MARKET_CODE = "J"  # KRX(공식 예제 확인 — ETF/ETN도 일반 주식과 동일 시장코드)


class KISEtfMixin:
    async def get_etf_price(self, etf_code: str) -> Ticker:
        """응답에 NAV(순자산가치) 등 ETF 전용 필드도 함께 오지만(§2 모델
        재사용 원칙) 소비하는 호출부가 생기기 전까지 Ticker의 표준
        필드만 채운다 — NAV 괴리율 등은 raw 응답이 필요해지면 별도
        메서드로 추가."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/etfetn/v1/quotations/inquire-price",
            "FHPST02400000",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": etf_code},
        )
        output = raw.get("output", {})
        price = Decimal(output.get("stck_prpr", "0"))
        return Ticker(
            symbol=etf_code,
            exchange="kis",
            price=price,
            bid=price,  # 응답에 최우선호가 없음(문서 관례) — 근사치
            ask=price,
            volume_24h=Decimal(output.get("acml_vol", "0")),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )
