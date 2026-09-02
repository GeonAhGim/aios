"""6.6 — BitgetAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트: GET /api/v2/spot/account/assets (2026-08-28 문서 조사 확인 —
실제 응답은 Demo API 키로 라이브 검증 필요, .env BITGET_API_KEY 채워지면
최우선 검증 대상).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.data.models.trading import AccountBalance, Position


class BitgetAccountMixin:
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        params: dict[str, Any] | None = {"coin": asset} if asset else None
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/account/assets", params=params
        )
        balances = []
        for item in raw["data"]:
            available = Decimal(item["available"])
            frozen = Decimal(item["frozen"])
            locked = Decimal(item.get("locked", "0"))
            balances.append(
                AccountBalance(
                    exchange="bitget",
                    asset=item["coin"].upper(),
                    total=available + frozen + locked,
                    available=available,
                    used_margin=frozen + locked,
                )
            )
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """스팟 거래소는 선물/마진과 달리 네이티브 "포지션" 개념이 없다 —
        AIOS 자체가 체결 내역으로부터 포지션을 내부적으로 계산·추적하고,
        Reconciliation(FD-9.6)은 get_balance()를 거래소측 진실 소스로
        사용한다. Phase 1은 Bitget spot만 대상(06번 §6.1)이므로 항상
        빈 리스트를 반환한다."""
        return []

    async def get_account_info(self) -> dict[str, Any]:
        """02b 스펙 §3.3(P1) — UID·권한(authorities) 확인용. 아직 소비하는
        호출부가 없어(§2 모델 재사용 원칙) raw dict 그대로 반환한다."""
        raw = await self._request("GET", "/api/v2/spot/account/info")  # type: ignore[attr-defined]
        return dict(raw["data"])

    async def get_account_bills(
        self, coin: str | None = None, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """02b 스펙 §3.3(P1) — FD-20(운용보고서) 원천 데이터. 청구서 행
        구조는 거래유형별로 필드가 달라(입금/출금/체결/이체 등) 아직
        모델화하지 않는다(get_fills와 동일 판단)."""
        params: dict[str, Any] = {"limit": str(limit)}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/account/bills", params=params
        )
        return list(raw["data"])

    async def transfer(
        self,
        from_type: str,
        to_type: str,
        amount: Decimal,
        coin: str,
        *,
        symbol: str | None = None,
    ) -> bool:
        """02b 스펙 §3.3(P1) — 현물↔선물 등 **AIOS 계정 내부** 자산 이체
        (FD-19 포트폴리오 재구성용). 7.9 원칙과 무관: 출금(외부 주소로의
        자산 유출)이 아니라 같은 계정 안의 자금 이동이다 — 별개 개념임을
        명확히 하기 위해 메서드명도 `withdraw`가 아닌 `transfer`로 둔다.
        `from_type`/`to_type`은 Bitget V2 문서 값 그대로 전달(예:
        "spot"/"usdt_futures"/"coin_futures"/"crossed_margin"/
        "isolated_margin") — 검증은 거래소 응답에 위임(§8.3 원칙)."""
        body: dict[str, Any] = {
            "fromType": from_type,
            "toType": to_type,
            "amount": str(amount),
            "coin": coin.upper(),
        }
        if symbol is not None:
            body["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/wallet/transfer", body=body
        )
        return bool(raw.get("code") == "00000")
