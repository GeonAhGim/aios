"""6.6 — BitgetAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.2.md#§2.1, L4_execution_oms_and_exchange_v1.0.md#L4-31

엔드포인트: GET /api/v2/spot/account/assets (2026-08-28 문서 조사 확인 —
2026-09-09 실키 실측(task-2514)으로 이 v2 엔드포인트가 UTA(Unified) 계정
에서 40085로 거부됨을 확인). CLASSIC 모드는 그대로 v2를 쓰고, UNIFIED
모드는 GET /api/v3/account/assets로 분기한다(docs/design/
02d_bitget_uta_v3_spec_v1.md — 응답 필드명은 공식 문서 조사 기준이며
실제 UTA 계정으로 왕복 검증 전까지 **미검증**, `_parse_v3_balance_row`
docstring 참고).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import ExchangeAPIError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, Position
from src.exchanges.bitget.account_mode import (
    AccountModeAwareClient,
    BitgetAccountMode,
    RequestSpec,
    account_aware_request,
)
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.http_client import SignedRequestClient

_QUOTE_CURRENCIES = ("USDT",)  # Phase 1 스콥(06번 §6.1) — USDT 마켓만


class _AccountModeClient(SignedRequestClient, AccountModeAwareClient, Protocol):
    """`self._request`(공통 계약) + `self.account_mode`(L4-31 상태) 둘 다
    필요한 계좌 메서드용 좁혀진 타입."""


class _TickerReadingClient(_AccountModeClient, Protocol):
    """get_positions()가 market_data_mixin의 get_ticker()를 교차 호출하고,
    같은 클래스의 get_balance()도 self가 이 좁혀진 타입인 채로 호출하므로
    둘 다 계약에 포함한다(공통 http_client.py는 이 스팟-전용 조합을 모른다)."""

    async def get_ticker(self, symbol: str) -> Ticker: ...
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...


def _parse_v2_balance_row(item: dict[str, Any]) -> AccountBalance:
    available = Decimal(item["available"])
    frozen = Decimal(item["frozen"])
    locked = Decimal(item.get("locked", "0"))
    return AccountBalance(
        exchange="bitget",
        asset=item["coin"].upper(),
        total=available + frozen + locked,
        available=available,
        used_margin=frozen + locked,
    )


def _parse_v3_balance_row(item: dict[str, Any]) -> AccountBalance:
    """**미검증**(task-2514) — UTA v3 `/api/v3/account/assets` 응답 필드는
    실제 UTA 계정으로 라이브 검증 전이다. 공식 문서 조사(2026-09-09) 기준
    행마다 `coin`/`available`/`locked`가 있고(v2의 `frozen`이 `locked`로
    통합된 것으로 보인다), v2에는 없던 `equity`/`usdValue`/`debt`가
    추가됐다는 정황이 있으나 이 어댑터가 아직 쓰지 않는 필드다."""
    available = Decimal(item.get("available", "0"))
    locked = Decimal(item.get("locked", "0"))
    return AccountBalance(
        exchange="bitget",
        asset=item["coin"].upper(),
        total=available + locked,
        available=available,
        used_margin=locked,
    )


def _v3_asset_rows(data: Any) -> list[dict[str, Any]]:
    """**미검증**(task-2514) — 공식 문서 조사 기준 `data.assets`가 배열로
    보이나, `data` 자체가 배열일 가능성도 배제할 수 없어 둘 다 받아들인다."""
    if isinstance(data, dict):
        return list(data.get("assets", []))
    return list(data)


class BitgetAccountMixin:
    async def get_balance(
        self: _AccountModeClient,
        asset: str | None = None,
    ) -> list[AccountBalance]:
        def build(mode: BitgetAccountMode) -> RequestSpec:
            params: dict[str, Any] | None = {"coin": asset} if asset else None
            path = (
                "/api/v2/spot/account/assets"
                if mode is BitgetAccountMode.CLASSIC
                else "/api/v3/account/assets"
            )
            return "GET", path, params, None

        raw = await account_aware_request(self, build)
        if self.account_mode is BitgetAccountMode.UNIFIED:
            return [_parse_v3_balance_row(item) for item in _v3_asset_rows(raw["data"])]
        return [_parse_v2_balance_row(item) for item in raw["data"]]

    async def get_positions(
        self: _TickerReadingClient, symbol: str | None = None
    ) -> list[Position]:
        """FULL_AUDIT_2026-09-02.md §2-B ④ — 이전엔 "스팟은 네이티브
        포지션이 없다"는 이유로 항상 빈 리스트였다. 하지만 그 이유는
        get_balance()를 진실 소스로 쓰는 이유는 되어도 get_positions()를
        영구히 비워두는 정당화는 아니다 — 이 메서드를 소비하는 호출부
        (Reconciliation 등)는 "포지션"이라는 형태로 조회하지, "잔고"라는
        형태로 다시 조회하지 않는다.

        보유 코인별로 Position을 합성한다 — 스팟 거래소는 평단가/미실현
        손익을 추적하지 않으므로(그건 AIOS 자체 전략 실행 기록의 몫)
        `average_entry_price`는 현재가로 대체한 자리표시자이고
        `unrealized_pnl`은 항상 0이다. quote 통화(USDT) 자체는 현금이지
        포지션이 아니라 제외한다. 코인마다 get_ticker() 호출이 필요해
        (N+1) 보유 종목이 많으면 느릴 수 있음 — Phase 1 스콥(06번 §6.1)
        전제상 보유 종목 수가 적다고 가정. 시세 조회에 실패한 코인은
        전체를 실패시키지 않고 건너뛴다(8.3 원칙 — 일부 실패가 전체
        조회를 막으면 안 됨)."""
        asset_filter = symbol.split("/")[0] if symbol else None
        balances = await self.get_balance(asset_filter)

        positions = []
        now = datetime.now(timezone.utc)
        for balance in balances:
            if balance.asset in _QUOTE_CURRENCIES or balance.total == 0:
                continue
            pair_symbol = f"{balance.asset}/USDT"
            try:
                ticker = await self.get_ticker(pair_symbol)
            except ExchangeAPIError:
                continue
            current_price = Money(amount=ticker.price, currency=Currency.USDT)
            positions.append(
                Position(
                    symbol=pair_symbol,
                    exchange="bitget",
                    strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
                    quantity=balance.total,
                    average_entry_price=current_price,  # 거래소가 평단가를 모름
                    current_price=current_price,
                    unrealized_pnl=Money(amount=Decimal("0"), currency=Currency.USDT),
                    realized_pnl=Money(amount=Decimal("0"), currency=Currency.USDT),
                    entry_time=now,
                    updated_at=now,
                    asset_class=AssetClass.CRYPTO,
                )
            )
        return positions

    async def get_trade_rate(
        self: SignedRequestClient, symbol: str, *, business_type: str = "spot"
    ) -> dict[str, Any]:
        """02b 스펙 §7(P1) — FD-8.2 수수료 미반영 Draft를 벗어날 때 필요.
        VIP 등급별 수수료율은 계정마다 달라(인증 필요) raw dict를 그대로
        반환한다(§2 모델 재사용 원칙, 소비하는 FD-8 호출부가 생기기 전까지
        모델화 보류)."""
        raw = await self._request(
            "GET",
            "/api/v2/common/trade-rate",
            params={"symbol": _to_bitget_symbol(symbol), "businessType": business_type},
        )
        return dict(raw["data"])

    async def get_account_info(self: _AccountModeClient) -> dict[str, Any]:
        """02b 스펙 §3.3(P1) — UID·권한(authorities) 확인용. 아직 소비하는
        호출부가 없어(§2 모델 재사용 원칙) raw dict 그대로 반환한다.

        L4-31(task-2514) — 이 엔드포인트가 실키 실측에서 실제로 40085를
        반환한 바로 그 호출이다(spec note). 응답 shape 자체는 v2/v3 모두
        raw dict 재수출이라 파싱 분기가 필요 없다 — 경로만 갈라진다."""

        def build(mode: BitgetAccountMode) -> RequestSpec:
            path = (
                "/api/v2/spot/account/info"
                if mode is BitgetAccountMode.CLASSIC
                else "/api/v3/account/info"
            )
            return "GET", path, None, None

        raw = await account_aware_request(self, build)
        return dict(raw["data"])

    async def get_account_bills(
        self: SignedRequestClient, coin: str | None = None, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """02b 스펙 §3.3(P1) — FD-20(운용보고서) 원천 데이터. 청구서 행
        구조는 거래유형별로 필드가 달라(입금/출금/체결/이체 등) 아직
        모델화하지 않는다(get_fills와 동일 판단)."""
        params: dict[str, Any] = {"limit": str(limit)}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(
            "GET", "/api/v2/spot/account/bills", params=params
        )
        return list(raw["data"])

    async def transfer(
        self: SignedRequestClient,
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
            body["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "POST", "/api/v2/spot/wallet/transfer", body=body
        )
        return bool(raw.get("code") == "00000")
