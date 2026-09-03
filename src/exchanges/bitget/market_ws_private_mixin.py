"""6.6 — BitgetAdapter Private WebSocket 채널 구독(orders/account/positions).

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§6

Private WebSocket(wss://ws.bitget.com/v2/ws/private, 공식 문서 기준 —
실제 채널 스키마는 라이브 검증 전까지 최선 추정치). 로그인 메시지는
`market_data_mixin.py::_build_login_message()` 참조.

2026-09-03 task-1032(PLT-40a 선행) — `market_data_mixin.py`(735줄, P6
line_cap 초과)에서 순수 이동(동작 변경 0). 연결관리는
`market_ws_connection.py`, 메시지 파싱은 `market_ws_parsing.py` 참조.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from src.data.models.trading import AccountBalance, Order, Position
from src.exchanges.bitget.market_data_mixin import _build_login_message
from src.exchanges.bitget.market_ws_connection import (
    ConnectFn,
    ReconnectHook,
    _connect,
    _run_ws_subscription,
)
from src.exchanges.bitget.market_ws_parsing import (
    parse_account_ws_message,
    parse_order_ws_message,
    parse_position_ws_message,
)
from src.exchanges.common.http_client import SignedRequestClient

logger = logging.getLogger(__name__)

WS_PRIVATE_URL = "wss://ws.bitget.com/v2/ws/private"

OrderCallback = Callable[[Order], Awaitable[None]]
AccountCallback = Callable[[AccountBalance], Awaitable[None]]
PositionCallback = Callable[[Position], Awaitable[None]]


class _PrivateWsClient(SignedRequestClient, Protocol):
    """Private WS 구독(order/account/positions)이 로그인 서명에 쓰는 API
    키 3종과, 재연결 후 재동기화에 쓰는 다른 믹스인(trading/account/
    futures_account)의 REST 메서드를 교차 호출하므로 필요한 계약(공통
    http_client.py는 이 스팟-전용 조합을 모른다)."""

    _api_key: str
    _api_secret: str
    _api_passphrase: str

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...
    async def get_futures_positions(
        self, *, product_type: str = ...
    ) -> list[Position]: ...


class BitgetMarketDataWsPrivateMixin:
    async def subscribe_order_stream(
        self: _PrivateWsClient,
        callback: OrderCallback,
        *,
        inst_type: str = "SPOT",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6/§9 작업분해 4 — Private `orders` 채널(P0). FD-4.5
        (UNKNOWN 재조회)를 폴링 대신 실시간 이벤트로 대체하는 근본
        해결책 — 이 채널이 붙으면 기존 3회 폴링 재시도 로직은 "최후의
        폴백"으로 격하되고 정상 경로는 실시간 확인이 된다(호출부 연결은
        별도 leaf). 로그인 메커니즘은 `_build_login_message()` docstring
        참조 — 라이브 검증 전까지 최선 추정치. `ExchangeAdapter` ABC에는
        아직 없음(다른 확장 메서드들과 동일 원칙, 모듈 docstring 참조)."""
        def _login() -> list[dict[str, Any]]:
            return [
                _build_login_message(
                    self._api_key,
                    self._api_secret,
                    self._api_passphrase,
                )
            ]

        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "orders", "instId": "default"}],
        }

        async def resync_then_notify() -> None:
            """FULL_AUDIT §2-B ② — 이 채널이 대체하려는 게 바로 FD-4.5
            폴링이므로(모듈독스트링 참조), 재연결 직후는 그 폴링이 가장
            필요한 순간이다 — 끊긴 동안 체결/거부됐을 미체결 주문을
            REST get_open_orders()로 재확인한다."""
            try:
                for order in await self.get_open_orders():
                    await callback(order)
            except Exception:  # noqa: BLE001 — 재동기화 실패로 재연결 자체를 막지 않음
                logger.warning("Bitget WS 재연결 후 미체결 주문 재동기화 실패")
            if on_reconnected is not None:
                await on_reconnected()

        async def on_message(message: dict[str, Any]) -> None:
            for order in parse_order_ws_message(message):
                await callback(order)

        await _run_ws_subscription(
            WS_PRIVATE_URL,
            subscribe_msg,
            on_message,
            pre_messages_factory=_login,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=resync_then_notify,
        )

    async def subscribe_account_stream(
        self: _PrivateWsClient,
        callback: AccountCallback,
        *,
        inst_type: str = "SPOT",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6(P1) — Private `account` 채널. FD-16.4(실행
        모니터링)이 현재 폴링 기반인 잔고 확인을 실시간으로 보강할 수
        있는 후보(호출부 연결은 별도 leaf). 로그인은
        `subscribe_order_stream`과 동일 메커니즘(§6 "Private 채널 로그인"
        절) — 라이브 검증 전까지 최선 추정치."""
        def _login() -> list[dict[str, Any]]:
            return [
                _build_login_message(
                    self._api_key,
                    self._api_secret,
                    self._api_passphrase,
                )
            ]

        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "account", "instId": "default"}],
        }

        async def resync_then_notify() -> None:
            """FULL_AUDIT §2-B ② — 재연결 후 REST get_balance()로 잔고
            재동기화(subscribe_ticker_stream과 동일 판단)."""
            try:
                for balance in await self.get_balance():
                    await callback(balance)
            except Exception:  # noqa: BLE001 — 재동기화 실패로 재연결 자체를 막지 않음
                logger.warning("Bitget WS 재연결 후 잔고 재동기화 실패")
            if on_reconnected is not None:
                await on_reconnected()

        async def on_message(message: dict[str, Any]) -> None:
            for balance in parse_account_ws_message(message):
                await callback(balance)

        await _run_ws_subscription(
            WS_PRIVATE_URL,
            subscribe_msg,
            on_message,
            pre_messages_factory=_login,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=resync_then_notify,
        )

    async def subscribe_positions_stream(
        self: _PrivateWsClient,
        callback: PositionCallback,
        *,
        inst_type: str = "USDT-FUTURES",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6(P1) — Private `positions` 채널(선물 전용). Phase 1은
        크립토 현물 전용(06번 §6.1)이라 아직 소비하는 호출부가 없다 —
        API 연동만 우선 완료해둔다(다른 확장 메서드와 동일 원칙)."""
        def _login() -> list[dict[str, Any]]:
            return [
                _build_login_message(
                    self._api_key,
                    self._api_secret,
                    self._api_passphrase,
                )
            ]

        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "positions", "instId": "default"}],
        }

        async def resync_then_notify() -> None:
            """FULL_AUDIT §2-B ② — 재연결 후 REST get_futures_positions()로
            포지션 재동기화(subscribe_ticker_stream과 동일 판단)."""
            try:
                for position in await self.get_futures_positions():
                    await callback(position)
            except Exception:  # noqa: BLE001 — 재동기화 실패로 재연결 자체를 막지 않음
                logger.warning("Bitget WS 재연결 후 포지션 재동기화 실패")
            if on_reconnected is not None:
                await on_reconnected()

        async def on_message(message: dict[str, Any]) -> None:
            for position in parse_position_ws_message(message):
                await callback(position)

        await _run_ws_subscription(
            WS_PRIVATE_URL,
            subscribe_msg,
            on_message,
            pre_messages_factory=_login,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=resync_then_notify,
        )
