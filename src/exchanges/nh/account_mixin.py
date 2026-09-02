"""NHAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§3

엔드포인트(2026-09-03 재확인, task-114 — 공식 OpenAPI 스펙
`https://www.nhplug.com/openapi-docs/krstock/openapi.json`을 도메인(SSOT)에서
직접 내려받아 확인, `nhplug-sdk` 레포 `docs/README.md`가 이 URL을 정본으로
지목): POST /krstock/inquiry/v1/balance, params {act_no, bnc_bse_cd:"5",
ltg_aot_dit_cd:"9", aet_bse:"2", qut_dit_cd:"UNT"}.

**응답 스키마 — 이전 추정과 실제로 다름이 확인됨**:
- 이전 구현은 `Output_2[].dpst_amt`를 예수금으로, `Output_1[].hld_qty`/
  `ord_psb_qty`를 보유/주문가능수량으로 추정했으나, 공식 스펙에는
  `Output_2` 자체가 없다(Output_0/Output_1만 존재).
- **예수금은 `Output_0.dca`**(단일 object, 계좌 현금 요약).
- **보유종목은 `Output_1[]`** — 종목코드는 `iem_cd`(추정과 일치했음),
  수량 필드는 `itg_bnc_qty`(통합잔고수량)와 `rsdl_qty`(잔량수량)이지
  `hld_qty`/`ord_psb_qty`가 아니다. `rsdl_qty`를 "주문 가능 수량"의
  최선 근사치로 쓴다 — 두 필드의 정확한 의미 차이(미결제수량 처리 등)는
  라이브 계좌로만 확정 가능해 **미검증**으로 남긴다.

필드 누락 시 조용히 0으로 채우지 않고 FatalExchangeError로 실패한다
(PM 배정 지침 (2)).
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
                qty = Decimal(str(row["itg_bnc_qty"]))
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
                        available=Decimal(str(row.get("rsdl_qty", qty))),
                        used_margin=Decimal("0"),
                    )
                )
            summary = raw.get("Output_0")
            if summary and "dca" in summary and (asset is None or asset == "KRW"):
                cash = Decimal(str(summary["dca"]))
                balances.append(
                    AccountBalance(
                        exchange="nh", asset="KRW", total=cash, available=cash,
                        used_margin=Decimal("0"),
                    )
                )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH balance 응답에 예상 필드 없음(공식 openapi.json 기준 "
                f"Output_0.dca/Output_1[].itg_bnc_qty 필요, 02e 스펙 §3 참조): {exc}"
            ) from exc
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Bitget/KIS와 동일 원칙(현물 거래소는 네이티브 포지션 개념이
        없다) — AIOS가 체결 내역으로부터 내부적으로 추적한다."""
        return []
