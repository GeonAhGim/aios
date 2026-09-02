"""02b_bitget_api_v2_full_spec_v1.md §5.2/§5.3 — BitgetAdapter Futures Account+Position 메서드군.

Spec: 02b_bitget_api_v2_full_spec_v1.md §5.2(Account)/§5.3(Position), P0

엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET  /api/v2/mix/account/accounts
- POST /api/v2/mix/account/set-leverage
- POST /api/v2/mix/account/set-margin-mode
- POST /api/v2/mix/account/set-position-mode
- GET  /api/v2/mix/account/liq-price
- GET  /api/v2/mix/position/single-position
- GET  /api/v2/mix/position/all-position
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import AccountBalance, Position
from src.exchanges.bitget.futures_market_mixin import DEFAULT_PRODUCT_TYPE
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol


class BitgetFuturesAccountMixin:
    async def get_futures_account(
        self,
        symbol: str,
        *,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> AccountBalance:
        """02b 스펙 §5.2(P0) — 단일 마진코인 계좌 조회. `get_futures_accounts()`
        (전체 조회)와 짝을 이루는 단건 조회(문서상 별도 엔드포인트)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/account/account",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
            },
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        available = Decimal(data.get("available", "0"))
        frozen = Decimal(data.get("locked", "0"))
        return AccountBalance(
            exchange="bitget",
            asset=data.get("marginCoin", margin_coin).upper(),
            total=Decimal(data.get("accountEquity", available + frozen)),
            available=available,
            used_margin=frozen,
        )

    async def get_futures_accounts(
        self, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[AccountBalance]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/account/accounts", params={"productType": product_type}
        )
        balances = []
        for item in raw["data"]:
            available = Decimal(item.get("available", "0"))
            frozen = Decimal(item.get("locked", "0"))
            balances.append(
                AccountBalance(
                    exchange="bitget",
                    asset=item["marginCoin"].upper(),
                    total=Decimal(item.get("accountEquity", available + frozen)),
                    available=available,
                    used_margin=frozen,
                )
            )
        return balances

    async def set_futures_leverage(
        self,
        symbol: str,
        leverage: Decimal,
        *,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> None:
        """8.2-A 설계 제약(ADR-2026-08-29-E §설계 제약 4) 재확인 — 이
        메서드는 API 연동일 뿐, "언제 어떤 레버리지를 쓸지"는 FD-8이 아직
        결정하지 않는다(현재 RiskEngine은 Phase 1 크립토 현물 전용이라
        레버리지를 항상 1.0으로 고정, src/core/risk/engine.py 참조).
        다자산군/파생상품 확장 시 FD-8.3 leverage 지표 재설계 후에만
        실제 호출부가 이 메서드를 쓰게 배선한다."""
        await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/account/set-leverage",
            body={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
                "leverage": str(leverage),
            },
        )

    async def set_futures_margin_mode(
        self,
        symbol: str,
        margin_mode: str,
        *,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> None:
        if margin_mode not in ("crossed", "isolated"):
            raise ValueError(f"알 수 없는 margin_mode입니다: {margin_mode!r}")
        await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/account/set-margin-mode",
            body={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
                "marginMode": margin_mode,
            },
        )

    async def set_futures_position_mode(
        self, position_mode: str, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> None:
        if position_mode not in ("one_way_mode", "hedge_mode"):
            raise ValueError(f"알 수 없는 position_mode입니다: {position_mode!r}")
        await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/account/set-position-mode",
            body={"productType": product_type, "posMode": position_mode},
        )

    async def get_futures_liquidation_price(
        self,
        symbol: str,
        *,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> Decimal:
        """FD-8.3(MDD/청산 위험) 계산 입력값 후보(02b §5.2) — 지금은 이
        값을 소비하는 RiskEngine 호출부가 없다(Phase 1 크립토 현물 전용,
        17.9-A). API 자체만 우선 연결."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/account/liq-price",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
            },
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return Decimal(data["liqPx"])

    async def set_futures_margin(
        self,
        symbol: str,
        amount: Decimal,
        *,
        hold_side: str | None = None,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> None:
        """02b 스펙 §5.2(P1) — 격리 모드 포지션의 증거금 증감. 음수 amount는
        감액(문서 관례, 라이브 검증 필요). `hold_side`는 헤지모드에서만
        필요("long"/"short")."""
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
            "marginCoin": margin_coin,
            "amount": str(amount),
        }
        if hold_side is not None:
            body["holdSide"] = hold_side
        await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/mix/account/set-margin", body=body
        )

    async def get_futures_max_open_amount(
        self,
        symbol: str,
        *,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> Decimal:
        """02b 스펙 §5.2(P1) — 현재 레버리지/잔고 기준 최대 개설가능수량."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/account/max-open",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
            },
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return Decimal(data["maxOpenAvailable"])

    async def get_futures_account_bills(
        self, *, product_type: str = DEFAULT_PRODUCT_TYPE, limit: int = 100
    ) -> list[dict[str, Any]]:
        """02b 스펙 §5.2(P1) — FD-20 보강용. raw dict 반환(get_fills와
        동일 판단, 거래유형별로 필드가 달라 모델화 보류)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/account/bill",
            params={"productType": product_type, "pageSize": str(limit)},
        )
        return list(raw["data"])

    async def get_futures_position_history(
        self, *, symbol: str | None = None, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[dict[str, Any]]:
        """02b 스펙 §5.3(P1) — 청산/전량청산된 과거 포지션. 현재 열려있는
        포지션(get_futures_position(s))과 달리 종료 시점 손익 요약 필드
        위주라 `Position` 모델과 형태가 달라 raw dict로 둔다."""
        params: dict[str, Any] = {"productType": product_type}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/position/history-position", params=params
        )
        return list(raw["data"])

    async def get_futures_position(
        self, symbol: str, *, margin_coin: str = "USDT", product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> Position | None:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/position/single-position",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
            },
        )
        rows = raw["data"]
        if not rows:
            return None
        return _row_to_position(rows[0], symbol)

    async def get_futures_positions(
        self, *, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[Position]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/position/all-position", params={"productType": product_type}
        )
        return [_row_to_position(row, row.get("symbol", "")) for row in raw["data"]]


def _row_to_position(data: dict[str, Any], symbol: str) -> Position:
    now = datetime.now(timezone.utc)
    return Position(
        symbol=symbol,
        exchange="bitget",
        strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함(get_order()와 동일 원칙)
        quantity=Decimal(data.get("total", "0")),
        average_entry_price=_to_money(data.get("openPriceAvg", "0")),
        current_price=_to_money(data.get("markPrice", data.get("openPriceAvg", "0"))),
        unrealized_pnl=_to_money(data.get("unrealizedPL", "0")),
        realized_pnl=_to_money(data.get("achievedProfits", "0")),
        leverage=Decimal(data.get("leverage", "1")),
        margin=_to_money(data["marginSize"]) if data.get("marginSize") is not None else None,
        entry_time=now,
        updated_at=now,
        asset_class=AssetClass.CRYPTO,
    )


def _to_money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.USDT)
