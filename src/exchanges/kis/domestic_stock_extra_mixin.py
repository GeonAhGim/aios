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

task-1781(BR-3)로 추가된 6개 신용잔고/대주/재무제표 엔드포인트도 같은
원칙 — 2026-09-07 공식 저장소 examples_llm/domestic_stock 하위 raw
소스(curl로 직접 확인, WebFetch 아님)의 tr_id/path/파라미터명을 그대로
옮겼다. 이 메서드들은 §2-B 공통 규칙에 따라 예상 output 키가 응답에
없으면(스키마 변경 등) 조용히 빈 값을 반환하지 않고 FatalExchangeError를
낸다 — 위 구세대 5개 메서드의 `.get(key, [])` 관례와 다르다.
"""
from __future__ import annotations

from typing import Any

from src.core.exceptions import FatalExchangeError

_MARKET_CODE = "J"  # KRX


def _require(raw: dict[str, Any], key: str, tr_id: str) -> Any:
    if key not in raw:
        raise FatalExchangeError(f"KIS {tr_id} 응답 파싱 실패({key} 없음): {raw}")
    return raw[key]


def _as_rows(value: Any) -> list[dict[str, Any]]:
    """공식 예제 코드 자체가 output1/output2를 리스트·단일 객체 양쪽으로
    방어 처리한다(예: lendable_by_company.py) — 어느 쪽으로 오든 리스트로
    정규화한다. 실제 응답이 항상 어느 형태인지는 미검증."""
    if isinstance(value, list):
        return list(value)
    return [value]


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

    async def get_credit_balance_ranking(
        self,
        *,
        symbol_scope: str = "0000",
        period: str = "2",
        rank_sort_code: str = "0",
    ) -> dict[str, list[dict[str, Any]]]:
        """국내주식 신용잔고 상위[국내주식-109].

        출처: examples_llm/domestic_stock/credit_balance/credit_balance.py
        (2026-09-07 raw 소스 확인) — tr_id FHKST17010000, path
        `/uapi/domestic-stock/v1/ranking/credit-balance`.
        `symbol_scope`: "0000"=전체,"0001"=거래소,"1001"=코스닥,"2001"=코스피200(원문).
        `rank_sort_code`: 0~4=융자 잔고 순위, 5~9=대주 잔고 순위(원문 docstring).
        """
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/ranking/credit-balance",
            "FHKST17010000",
            params={
                "FID_COND_SCR_DIV_CODE": "11701",
                "FID_INPUT_ISCD": symbol_scope,
                "FID_OPTION": period,
                "FID_COND_MRKT_DIV_CODE": _MARKET_CODE,
                "FID_RANK_SORT_CLS_CODE": rank_sort_code,
            },
        )
        return {
            "output1": _as_rows(_require(raw, "output1", "FHKST17010000")),
            "output2": _as_rows(_require(raw, "output2", "FHKST17010000")),
        }

    async def get_daily_credit_balance(
        self, symbol: str, *, settle_date: str
    ) -> list[dict[str, Any]]:
        """국내주식 신용잔고 일별추이[국내주식-110]. 상환수량은 매도상환수량
        +현금상환수량 합계(원문 주석).

        출처: examples_llm/domestic_stock/daily_credit_balance/
        daily_credit_balance.py (2026-09-07 raw 소스 확인) — tr_id
        FHPST04760000, path
        `/uapi/domestic-stock/v1/quotations/daily-credit-balance`.
        한 번의 호출에 최대 30건, `settle_date`로 다음 조회 가능(원문 docstring,
        이 메서드는 페이지네이션을 자동으로 하지 않는다).
        """
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/daily-credit-balance",
            "FHPST04760000",
            params={
                "FID_COND_MRKT_DIV_CODE": _MARKET_CODE,
                "FID_COND_SCR_DIV_CODE": "20476",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": settle_date,
            },
        )
        return _as_rows(_require(raw, "output", "FHPST04760000"))

    async def get_lendable_by_company(
        self,
        *,
        exchange_code: str = "00",
        symbol: str = "",
        company_lendable_only: bool = True,
        sort_by_symbol: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """당사 대주가능 종목[국내주식-195].

        출처: examples_llm/domestic_stock/lendable_by_company/
        lendable_by_company.py (2026-09-07 raw 소스 확인) — tr_id
        CTSC2702R, path
        `/uapi/domestic-stock/v1/quotations/lendable-by-company`.
        `exchange_code`: "00"=전체,"02"=거래소,"03"=코스닥(원문 docstring).
        """
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/lendable-by-company",
            "CTSC2702R",
            params={
                "EXCG_DVSN_CD": exchange_code,
                "PDNO": symbol,
                "THCO_STLN_PSBL_YN": "Y" if company_lendable_only else "N",
                "INQR_DVSN_1": "1" if sort_by_symbol else "0",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK100": "",
            },
        )
        return {
            "output1": _as_rows(_require(raw, "output1", "CTSC2702R")),
            "output2": _as_rows(_require(raw, "output2", "CTSC2702R")),
        }

    async def get_credit_by_company(
        self,
        *,
        symbol_scope: str = "0000",
        orderable_only: bool = True,
        sort_by_symbol: bool = False,
    ) -> list[dict[str, Any]]:
        """국내주식 당사 신용가능종목[국내주식-111].

        출처: examples_llm/domestic_stock/credit_by_company/
        credit_by_company.py (2026-09-07 raw 소스 확인) — tr_id
        FHPST04770000, path
        `/uapi/domestic-stock/v1/quotations/credit-by-company`.
        파라미터명이 소문자(fid_...)인 것은 원문 그대로다 — 이 mixin의
        다른 메서드와 달리 대문자가 아니다. KIS 쿼리 파라미터는 통상
        대소문자 무관이라 실동작에는 영향 없을 것으로 보이나 미검증.
        """
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/quotations/credit-by-company",
            "FHPST04770000",
            params={
                "fid_rank_sort_cls_code": "1" if sort_by_symbol else "0",
                "fid_slct_yn": "0" if orderable_only else "1",
                "fid_input_iscd": symbol_scope,
                "fid_cond_scr_div_code": "20477",
                "fid_cond_mrkt_div_code": _MARKET_CODE,
            },
        )
        return _as_rows(_require(raw, "output", "FHPST04770000"))

    async def get_financial_balance_sheet(
        self, symbol: str, *, period_div_code: str = "0"
    ) -> list[dict[str, Any]]:
        """국내주식 대차대조표(재무상태표)[v1_국내주식-078].

        출처: examples_llm/domestic_stock/finance_balance_sheet/
        finance_balance_sheet.py (2026-09-07 raw 소스 확인) — tr_id
        FHKST66430100, path `/uapi/domestic-stock/v1/finance/balance-sheet`.
        `period_div_code`: "0"=연간,"1"=분기(원문 docstring, get_financial_ratio와
        동일 관례). 파라미터 대소문자도 원문 그대로(FID_DIV_CLS_CODE만 대문자).
        """
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/finance/balance-sheet",
            "FHKST66430100",
            params={
                "FID_DIV_CLS_CODE": period_div_code,
                "fid_cond_mrkt_div_code": _MARKET_CODE,
                "fid_input_iscd": symbol,
            },
        )
        return _as_rows(_require(raw, "output", "FHKST66430100"))

    async def get_income_statement(
        self, symbol: str, *, period_div_code: str = "0"
    ) -> list[dict[str, Any]]:
        """국내주식 손익계산서[v1_국내주식-079].

        출처: examples_llm/domestic_stock/finance_income_statement/
        finance_income_statement.py (2026-09-07 raw 소스 확인) — tr_id
        FHKST66430200, path `/uapi/domestic-stock/v1/finance/income-statement`.
        `period_div_code`: "0"=연간,"1"=분기(원문 docstring). 파라미터
        대소문자도 원문 그대로(FID_DIV_CLS_CODE만 대문자).
        """
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/finance/income-statement",
            "FHKST66430200",
            params={
                "FID_DIV_CLS_CODE": period_div_code,
                "fid_cond_mrkt_div_code": _MARKET_CODE,
                "fid_input_iscd": symbol,
            },
        )
        return _as_rows(_require(raw, "output", "FHKST66430200"))

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
