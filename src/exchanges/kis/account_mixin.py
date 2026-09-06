"""6.10 — KISAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트: GET /uapi/domestic-stock/v1/trading/inquire-balance,
tr_id TTTC8434R(모의투자는 어댑터가 자동으로 VTTC8434R로 치환) — 2026-08-28
KIS 공식 GitHub 예제 소스코드 확인. output1=보유종목별 리스트,
output2=계좌 요약(예수금 등, 실제 필드명은 라이브 검증 필요).

task-1781(BR-3) — get_period_trade_profit/get_period_profit 추가.
2026-09-07 공식 저장소 examples_llm/domestic_stock 하위 raw 소스(curl로
직접 확인)의 tr_id/path/파라미터명을 그대로 옮겼다. 두 TR 모두 BR-1
매트릭스(docs/design/KIS_TR_COVERAGE.md)에서 "실전계좌필요"로 분류됨
— 공식 예제 저장소에 모의투자(V-접두) 버전 예제가 없다는 뜻이며, 모의
투자 서버가 실제로 이 TR에 응답하는지는 미검증이다. 어댑터의 표준
T→V 치환 규칙(adapter.py `_resolve_tr_id`)은 그대로 적용된다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import AccountBalance, Position
from src.exchanges.common.http_client import KISHTTPClient

_BALANCE_PARAMS = {
    "AFHR_FLPR_YN": "N",
    "OFL_YN": "",
    "INQR_DVSN": "02",  # 종목별
    "UNPR_DVSN": "01",
    "FUND_STTL_ICLD_YN": "N",
    "FNCG_AMT_AUTO_RDPT_YN": "N",
    "PRCS_DVSN": "00",
    "CTX_AREA_FK100": "",
    "CTX_AREA_NK100": "",
}


class KISAccountMixin:
    async def get_balance(self: KISHTTPClient, asset: str | None = None) -> list[AccountBalance]:
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            "TTTC8434R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                **_BALANCE_PARAMS,
            },
        )
        balances = []
        for row in raw.get("output1", []):
            qty = Decimal(row.get("hldg_qty", "0"))
            if qty == 0:
                continue  # 당일 전량매도 등으로 남은 0잔량 행은 제외(원문 참고사항)
            stock_code = row["pdno"]
            if asset is not None and stock_code != asset:
                continue
            available = Decimal(row.get("ord_psbl_qty", row["hldg_qty"]))
            balances.append(
                AccountBalance(
                    exchange="kis",
                    asset=stock_code,
                    total=qty,
                    available=available,
                    used_margin=Decimal("0"),
                )
            )

        # output2 — 계좌 요약(예수금). 실제 필드명(dnca_tot_amt)은 라이브 검증 필요.
        for summary in raw.get("output2", []):
            if "dnca_tot_amt" in summary and (asset is None or asset == "KRW"):
                cash = Decimal(summary["dnca_tot_amt"])
                balances.append(
                    AccountBalance(
                        exchange="kis",
                        asset="KRW",
                        total=cash,
                        available=cash,
                        used_margin=Decimal("0"),
                    )
                )
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Bitget과 동일 원칙(account_mixin.py 참조) — 거래소가 AIOS의
        전략별 컨텍스트를 모르므로 항상 빈 리스트. 실제 보유종목은
        get_balance()로 조회 가능(Reconciliation 진실 소스)."""
        return []

    async def get_period_trade_profit(
        self: KISHTTPClient,
        *,
        start_date: str,
        end_date: str,
        sort_dvsn: str = "00",
        balance_dvsn: str = "00",
        symbol: str = "",
    ) -> dict[str, Any]:
        """기간별매매손익현황조회[v1_국내주식-060] — HTS [0856] 화면의
        "종목별" 탭에 대응(원문 docstring).

        출처: examples_llm/domestic_stock/inquire_period_trade_profit/
        inquire_period_trade_profit.py (2026-09-07 raw 소스 확인) —
        tr_id TTTC8715R, path
        `/uapi/domestic-stock/v1/trading/inquire-period-trade-profit`.
        `sort_dvsn`: "00"/"02"=최근순,"01"=과거순(원문). output1=종목별
        리스트, output2=합계(단일 객체) — 응답에 둘 중 하나라도 없으면
        조용히 빈 값을 반환하지 않고 예외를 낸다.
        """
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit",
            "TTTC8715R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "SORT_DVSN": sort_dvsn,
                "INQR_STRT_DT": start_date,
                "INQR_END_DT": end_date,
                "CBLC_DVSN": balance_dvsn,
                "PDNO": symbol,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        if "output1" not in raw or "output2" not in raw:
            raise FatalExchangeError(
                f"KIS 기간별매매손익현황 응답 파싱 실패(output1/output2 없음): {raw}"
            )
        return {"items": list(raw["output1"]), "summary": dict(raw["output2"])}

    async def get_period_profit(
        self: KISHTTPClient,
        *,
        start_date: str,
        end_date: str,
        sort_dvsn: str = "00",
        inquiry_dvsn: str = "00",
        balance_dvsn: str = "00",
        symbol: str = "",
    ) -> dict[str, Any]:
        """기간별손익일별합산조회[v1_국내주식-052] — HTS [0856] 화면의
        "일별" 탭에 대응(원문 docstring).

        출처: examples_llm/domestic_stock/inquire_period_profit/
        inquire_period_profit.py (2026-09-07 raw 소스 확인) — tr_id
        TTTC8708R, path
        `/uapi/domestic-stock/v1/trading/inquire-period-profit`.
        output1=일별 리스트, output2=합계(단일 객체) — get_period_trade_profit과
        동일하게 응답에 둘 중 하나라도 없으면 예외를 낸다.
        """
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-period-profit",
            "TTTC8708R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": start_date,
                "INQR_END_DT": end_date,
                "SORT_DVSN": sort_dvsn,
                "INQR_DVSN": inquiry_dvsn,
                "CBLC_DVSN": balance_dvsn,
                "PDNO": symbol,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        if "output1" not in raw or "output2" not in raw:
            raise FatalExchangeError(
                f"KIS 기간별손익일별합산조회 응답 파싱 실패(output1/output2 없음): {raw}"
            )
        return {"items": list(raw["output1"]), "summary": dict(raw["output2"])}
