"""6.10 — KISAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트: GET /uapi/domestic-stock/v1/trading/inquire-balance,
tr_id TTTC8434R(모의투자는 어댑터가 자동으로 VTTC8434R로 치환) — 2026-08-28
KIS 공식 GitHub 예제 소스코드 확인. output1=보유종목별 리스트,
output2=계좌 요약(예수금 등, 실제 필드명은 라이브 검증 필요).
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.trading import AccountBalance, Position

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
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            "TTTC8434R",
            params={
                "CANO": self._cano,  # type: ignore[attr-defined]
                "ACNT_PRDT_CD": self._acnt_prdt_cd,  # type: ignore[attr-defined]
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
