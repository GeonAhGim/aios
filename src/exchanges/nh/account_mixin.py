"""NHAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§3

엔드포인트(2026-09-03 공식 SDK 소스코드 확인): POST
/krstock/inquiry/v1/balance, params {act_no, bnc_bse_cd:"5",
ltg_aot_dit_cd:"9", aet_bse:"2", qut_dit_cd:"UNT"}.

⚠️ market_data_mixin.py와 동일 caveat — 요청 파라미터는 확인됐지만
응답 필드명은 SDK 스니펫에 없었다. KIS 잔고 응답의 관례(보유수량/
주문가능수량/예수금 필드 구성)를 최선 추정치로 사용한다 — 라이브
검증 전까지 확정 아님. 필드 누락 시 조용히 0으로 채우지 않고
FatalExchangeError로 실패한다(PM 배정 지침 (2)).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import AccountBalance, Position


class NHAccountMixin:
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/krstock/inquiry/v1/balance",
            body={
                "act_no": self._act_no,  # type: ignore[attr-defined]
                "bnc_bse_cd": "5",
                "ltg_aot_dit_cd": "9",
                "aet_bse": "2",
                "qut_dit_cd": "UNT",
            },
        )
        balances = []
        try:
            for row in raw.get("Output_1", []):
                qty = Decimal(row["hld_qty"])
                if qty == 0:
                    continue
                stock_code = row["iem_cd"]
                if asset is not None and stock_code != asset:
                    continue
                balances.append(
                    AccountBalance(
                        exchange="nh",
                        asset=stock_code,
                        total=qty,
                        available=Decimal(row.get("ord_psb_qty", str(qty))),
                        used_margin=Decimal("0"),
                    )
                )
            for summary in raw.get("Output_2", []):
                if "dpst_amt" in summary and (asset is None or asset == "KRW"):
                    cash = Decimal(summary["dpst_amt"])
                    balances.append(
                        AccountBalance(
                            exchange="nh", asset="KRW", total=cash, available=cash,
                            used_margin=Decimal("0"),
                        )
                    )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH balance 응답에 예상 필드 없음(응답 스키마 미확인 — "
                f"02e 스펙 §3 caveat 참조): {exc}"
            ) from exc
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Bitget/KIS와 동일 원칙(현물 거래소는 네이티브 포지션 개념이
        없다) — AIOS가 체결 내역으로부터 내부적으로 추적한다."""
        return []
