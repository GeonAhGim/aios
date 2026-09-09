"""6.6 — BitgetAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.2.md#§2.1, L4_execution_oms_and_exchange_v1.0.md#L4-31

엔드포인트: GET /api/v2/spot/account/assets (2026-08-28 문서 조사 확인 —
2026-09-09 real-key empirical test (task-2514) confirmed this v2 endpoint is
rejected with 40085 on a UTA (Unified) account). CLASSIC mode keeps using v2,
while UNIFIED mode branches to GET /api/v3/account/assets
(docs/design/02d_bitget_uta_v3_spec_v1.md — response field names follow
official documentation research and are **UNVERIFIED** until confirmed
against a real UTA account round trip; see `_parse_v3_balance_row`
docstring).
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
from src.exchanges.common.live_guard import require_paper_sandbox

_QUOTE_CURRENCIES = ("USDT",)  # Phase 1 스콥(06번 §6.1) — USDT 마켓만


class _AccountModeClient(SignedRequestClient, AccountModeAwareClient, Protocol):
    """Narrowed type for account methods that need both `self._request` (the
    common contract) and `self.account_mode` (L4-31 state)."""


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
    """**UNVERIFIED** (task-2514) — UTA v3 `/api/v3/account/assets` response
    fields have not yet been live-verified against a real UTA account. Per
    official documentation research (2026-09-09), each row appears to have
    `coin`/`available`/`locked` (v2's `frozen` appears to have been merged
    into `locked`), and there are indications that `equity`/`usdValue`/`debt`,
    absent in v2, were added — but these are fields this adapter does not yet
    use."""
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
    """**UNVERIFIED** (task-2514) — per official documentation research,
    `data.assets` appears to be an array, but the possibility that `data`
    itself is an array cannot be ruled out, so both are accepted."""
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
        no consuming caller yet (§2 model-reuse principle), so it returns the
        raw dict as-is.

        L4-31 (task-2514) — this is the exact endpoint call that actually
        returned 40085 during the real-key empirical test (spec note). The
        response shape itself is a raw dict re-export for both v2/v3, so no
        parsing branch is needed — only the path differs."""

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

    @require_paper_sandbox
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
        "isolated_margin") — validation is delegated to the exchange response
        (§8.3 principle).

        esc-2514 (task-2530) — a P0 defect where, even though this is an
        extension method that bypasses the Executor, `@require_paper_sandbox`
        was missing, making real fund movement possible even on the LIVE
        adapter (same class as red-team #2026-09-02-32; fixed with the same
        pattern as convert_mixin.py::execute_convert)."""
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
