"""02d_kis_api_full_spec_v1.md §3 — KISAdapter 국내주식 조회 확장(P1) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §3, §7(작업 분해 2번)

market_data_mixin.py/trading_mixin.py의 핵심 시세·매매 흐름과 달리
재무/수급/공시 성격의 조회라 별도 파일로 분리한다(최소모듈 원칙).
아래 5개 엔드포인트는 이번 조사(WebFetch, github.com/koreainvestment/
open-trading-api/examples_llm/domestic_stock, 2026-09-02)로 실제 예제
소스코드의 tr_id/path/파라미터명을 직접 확인했다 — Bitget 커뮤니티
SDK 수준의 "최선 추정치"가 아니라 공식 예제 코드 확인이지만, 실제
응답 필드명은 여전히 라이브 검증 전이라 raw dict로 반환한다(§2 모델
재사용 원칙 — 이 데이터를 소비하는 FD 호출부가 아직 없음).
"""
from __future__ import annotations

from typing import Any

_MARKET_CODE = "J"  # KRX


class KISDomesticStockExtraMixin:
    async def get_investor_trend_estimate(self, symbol: str) -> list[dict[str, Any]]:
        """장중 추정 투자자별(외국인/기관) 매매동향."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
            "HHPTJ04160200",
            params={"MKSC_SHRN_ISCD": symbol},
        )
        return list(raw.get("output2", []))

    async def get_financial_ratio(
        self, symbol: str, *, period_div_code: str = "0"
    ) -> list[dict[str, Any]]:
        """`period_div_code`: "0"=연간, "1"=분기(문서 관례)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/finance/financial-ratio",
            "FHKST66430300",
            params={
                "FID_DIV_CLS_CODE": period_div_code,
                "FID_COND_MRKT_DIV_CODE": _MARKET_CODE,
                "FID_INPUT_ISCD": symbol,
            },
        )
        return list(raw.get("output", []))

    async def get_investor_trading_by_stock(self, symbol: str) -> dict[str, Any]:
        """개인/외국인/기관 매매 현황(현재가 기준 단일 조회)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            params={"FID_COND_MRKT_DIV_CODE": _MARKET_CODE, "FID_INPUT_ISCD": symbol},
        )
        return dict(raw.get("output", {}))

    async def get_dividend_disclosures(
        self,
        *,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        high_dividend_only: bool = False,
    ) -> list[dict[str, Any]]:
        """배당 공시 정보(KSD 예탁결제원 제공). `symbol` 생략 시 전체
        종목 대상(문서 관례 — 공백 문자열)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/ksdinfo/dividend",
            "HHKDB669102C0",
            params={
                "CTS": "",
                "GB1": "0",
                "F_DT": start_date or "",
                "T_DT": end_date or "",
                "SHT_CD": symbol or "",
                "HIGH_GB": "1" if high_dividend_only else "",
            },
        )
        return list(raw.get("output", []))

    async def get_program_trade_daily(
        self,
        market_class_code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """`market_class_code`: "K"=코스피, "Q"=코스닥(문서 관례). 프로그램
        매매(차익/비차익) 일별 동향 — 시장 전체 신호 보강용."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily",
            "FHPPG04600001",
            params={
                "FID_COND_MRKT_DIV_CODE": _MARKET_CODE,
                "FID_MRKT_CLS_CODE": market_class_code,
                "FID_INPUT_DATE_1": start_date or "",
                "FID_INPUT_DATE_2": end_date or "",
            },
        )
        return list(raw.get("output", []))
