"""L4-20 — bitget private WS `orders` 채널의 체결 이벤트 → inbox 배선.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-20, §10 U3.

선행: L4-15(task-1553 46f350e, `application/inbox_processor.py`) ·
L4-19(task-1551 1864403, `exchanges/common/ws_session.py`). 연결 관리
(하트비트·ack 검증·재연결·seq 갭)는 재구현하지 않는다 —
`market_ws_connection.py::_run_ws_subscription`(→ 공용 `WsSession`)을
그대로 재사용한다. 이 모듈이 새로 하는 일은 하나뿐이다: Private
`orders` 채널의 원시 행(raw dict)을 `ProviderOrderEvent`로 바꿔
`InboxProcessor.ingest()`에 먹인다.

이 경로는 `market_ws_private_mixin.py::subscribe_order_stream`(REST
조회용 `Order` 도메인 모델로 파싱, `_row_to_order()` 재사용)과는
별개다 — 그쪽은 `tradeId`/`fillPrice`/`feeDetail` 같은 체결 전용
필드를 `Order` 모델에 담을 자리가 없어 버린다. 이 모듈은 원시 행을
직접 다뤄 그 필드를 보존한다.

미검증(§10 U3): Bitget `orders` 채널이 체결마다 `tradeId`/`fillPrice`/
`baseVolume`(그 체결분 수량)을 함께 보내는지, 아니면 uTime마다 누적
필드만 갱신하는지는 공식 문서로 확인하지 못했다 — 커뮤니티 SDK 관례를
따라 `tradeId`가 있고 가격·수량이 채워진 행만 체결로 간주한다. 실패
시 폴링 경로(inbox `source=POLL`, 별도 리프)가 정상 경로다.

fail-closed 원칙(FULL_AUDIT §11 "조용한 드롭 금지"): 필수 식별자
(주문 ID)나 체결 수량·가격이 빠진 행도 이 모듈이 스스로 버리지
않는다 — 항상 `ProviderOrderEvent`를 만들어 inbox에 넣는다(raw_hash
기반 provider_event_id로 감사 가능). 매칭·적용 여부 판정은 이미
L4-15가 하는 일이라 여기서 다시 하지 않는다 — `InboxProcessor.
_process_row`가 매칭 안 되는 이벤트를 IGNORED로 남기는 fail-closed
경로를 그대로 탄다(조용한 카운터가 아니라 감사 가능한 행).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.data.models.trading import OrderSide
from src.exchanges.bitget.market_data_mixin import _build_login_message
from src.exchanges.bitget.market_ws_connection import ConnectFn, _connect, _run_ws_subscription
from src.exchanges.bitget.trading_query_mixin import _parse_bitget_timestamp
from src.exchanges.common.http_client import SignedRequestClient
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent

logger = logging.getLogger(__name__)

WS_PRIVATE_URL = "wss://ws.bitget.com/v2/ws/private"

_LIQUIDITY_MAP = {"T": "TAKER", "M": "MAKER"}


class PrivateWsInboxClient(SignedRequestClient, Protocol):
    """이 모듈이 로그인 서명에 쓰는 API 키 3종만 요구한다 — `subscribe_
    order_stream`의 `_PrivateWsClient`처럼 REST 재동기화 메서드는 필요
    없다(이 리프는 체결 이벤트만 다룬다, 모듈 docstring 참조)."""

    _api_key: str
    _api_secret: str
    _api_passphrase: str


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _extract_fill(row: dict[str, Any], *, exchange_order_id: str | None) -> FillEvent | None:
    """`tradeId`가 있고 가격·수량이 둘 다 채워진 행만 체결로 본다(모듈
    docstring §10 U3). 하나라도 없으면 이 행은 체결이 아니라 순수 상태
    갱신(예: live→cancelled)으로 취급 — `None`을 돌려주되 호출부는 이를
    "버림"이 아니라 "체결 아님"으로 다룬다(이벤트 자체는 여전히 inbox로 간다)."""
    trade_id = row.get("tradeId")
    quantity = _decimal(row.get("baseVolume") or row.get("fillSize"))
    price = _decimal(row.get("fillPrice"))
    if not trade_id or quantity is None or price is None or exchange_order_id is None:
        return None
    fee = Decimal("0")
    fee_currency = ""
    fee_detail = row.get("feeDetail")
    if isinstance(fee_detail, list) and fee_detail and isinstance(fee_detail[0], dict):
        first = fee_detail[0]
        fee = abs(_decimal(first.get("totalFee")) or Decimal("0"))
        fee_currency = str(first.get("feeCoin") or "").upper()
    side_raw = str(row.get("side", "")).upper()
    known_sides = (OrderSide.BUY.value, OrderSide.SELL.value)
    side = OrderSide(side_raw) if side_raw in known_sides else OrderSide.BUY
    liquidity = _LIQUIDITY_MAP.get(str(row.get("tradeScope", "")).upper(), "UNKNOWN")
    return FillEvent(
        provider_fill_id=str(trade_id),
        venue="bitget",
        order_id=None,
        exchange_order_id=exchange_order_id,
        symbol=str(row.get("instId", "")),
        side=side,
        quantity=quantity,
        price=price,
        fee=fee,
        fee_currency=fee_currency,
        liquidity=liquidity,  # type: ignore[arg-type]
        venue_ts=_parse_bitget_timestamp(row.get("uTime") or row.get("cTime")),
    )


def parse_private_order_row(row: dict[str, Any], *, received_at: datetime) -> ProviderOrderEvent:
    """원시 `orders` 채널 행 하나 → `ProviderOrderEvent`. 절대 예외를
    던지거나 `None`을 돌려주지 않는다(모듈 docstring "조용한 드롭
    금지") — 식별자가 전혀 없으면 `raw_hash` 기반 provider_event_id로
    감사 가능한 행을 만든다."""
    exchange_order_id_raw = row.get("orderId")
    client_order_id_raw = row.get("clientOid")
    exchange_order_id = str(exchange_order_id_raw) if exchange_order_id_raw else None
    client_order_id = str(client_order_id_raw) if client_order_id_raw else None
    raw_hash = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()

    fill = _extract_fill(row, exchange_order_id=exchange_order_id)
    if fill is not None:
        provider_event_id = f"bitget:orders:fill:{fill.provider_fill_id}"
    elif exchange_order_id is not None:
        provider_event_id = f"bitget:orders:{exchange_order_id}:{row.get('uTime') or raw_hash}"
    else:
        # 주문 ID가 전혀 없다 — 그래도 감사 가능한 행은 남긴다(fail-closed).
        provider_event_id = f"bitget:orders:unresolved:{raw_hash}"

    filled_quantity = _decimal(row.get("fillSize") or row.get("baseVolume")) or Decimal("0")

    return ProviderOrderEvent(
        provider_event_id=provider_event_id,
        venue="bitget",
        venue_symbol=str(row.get("instId", "")),
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        venue_status=str(row.get("status", "")),
        filled_quantity=filled_quantity,
        average_price=_decimal(row.get("priceAvg")),
        last_fill=fill,
        venue_ts=_parse_bitget_timestamp(row.get("uTime") or row.get("cTime")),
        received_at=received_at,
        source="WS",
        raw_hash=raw_hash,
    )


async def subscribe_bitget_orders_to_inbox(
    client: PrivateWsInboxClient,
    inbox: InboxProcessor,
    *,
    inst_type: str = "SPOT",
    connect_fn: ConnectFn = _connect,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ping_sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Private `orders` 채널을 구독해 체결·상태 이벤트를 inbox로 흘려보낸다.
    `client`는 서명 3종(`_api_key`/`_api_secret`/`_api_passphrase`)만 있으면
    되므로 `BitgetAdapter` 인스턴스를 그대로 넘기면 된다(mixin으로
    엮지 않는 이유: `adapter.py`의 상속 목록은 이 리프의 파일 범위 밖 —
    decision "wiring.py 수정은 구독 등록 1블록으로 제한"과 같은 원칙으로
    이 함수도 독립 함수로 둔다). 재연결/하트비트/ack/seq 갭은 전부
    `_run_ws_subscription`(→ 공용 `WsSession`)이 처리한다 — 이 함수는
    로그인 메시지 조립과 파싱만 담당한다."""

    def _login() -> list[dict[str, Any]]:
        return [_build_login_message(client._api_key, client._api_secret, client._api_passphrase)]

    subscribe_msg = {
        "op": "subscribe",
        "args": [{"instType": inst_type, "channel": "orders", "instId": "default"}],
    }

    async def on_message(message: dict[str, Any]) -> None:
        for row in message.get("data", []):
            if not isinstance(row, dict):
                continue
            ev = parse_private_order_row(row, received_at=datetime.now(timezone.utc))
            await inbox.ingest(ev)

    await _run_ws_subscription(
        WS_PRIVATE_URL,
        subscribe_msg,
        on_message,
        pre_messages_factory=_login,
        connect_fn=connect_fn,
        sleep_fn=sleep_fn,
        ping_sleep_fn=ping_sleep_fn,
    )
