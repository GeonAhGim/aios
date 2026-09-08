"""L4-20 -- Bitget private WS `orders` channel fill events -> inbox wiring.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md #9 L4-20, #10 U3.

Depends on: L4-15 (task-1553 46f350e, `application/inbox_processor.py`) and
L4-19 (task-1551 1864403, `exchanges/common/ws_session.py`). Connection
management (heartbeat, ack validation, reconnect, seq gap) is not
reimplemented -- `market_ws_connection.py::_run_ws_subscription` (-> the
shared `WsSession`) is reused as-is. This module does exactly one new
thing: turn a raw private `orders` channel row (dict) into a
`ProviderOrderEvent` and feed it to `InboxProcessor.ingest()`.

This path is separate from `market_ws_private_mixin.py::
subscribe_order_stream` (parses into the REST-facing `Order` domain
model via `_row_to_order()`) -- that path has no place to keep
fill-only fields such as `tradeId`/`fillPrice`/`feeDetail`, so it drops
them. This module handles the raw row directly and keeps those fields.

Unverified (#10 U3): whether Bitget's `orders` channel sends
`tradeId`/`fillPrice`/`baseVolume` (that fill's quantity) together on
every fill, or only updates cumulative fields per `uTime`, could not be
confirmed against official docs -- following community SDK convention,
a row is treated as a fill only when `tradeId` is present and price/
quantity are both populated. If this assumption is wrong, the polling
path (inbox `source=POLL`, a separate leaf) is the path of record.

Fail-closed principle (FULL_AUDIT #11 "no silent drops"): a row missing
a required identifier (order id) or fill quantity/price is not dropped
by this module either -- it always builds a `ProviderOrderEvent` and
pushes it into the inbox (auditable via a raw_hash-based
provider_event_id). Matching/applicability is already L4-15's job, so
it is not repeated here -- unmatched events ride the same fail-closed
path `InboxProcessor._process_row` already uses to leave them IGNORED
(an auditable row, not a silent counter).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from src.data.models.trading import OrderSide
from src.exchanges.bitget.market_data_mixin import _build_login_message
from src.exchanges.bitget.market_ws_connection import ConnectFn, _connect, _run_ws_subscription
from src.exchanges.bitget.trading_query_mixin import _parse_bitget_timestamp
from src.exchanges.common.http_client import SignedRequestClient
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent

logger = logging.getLogger(__name__)

WS_PRIVATE_URL = "wss://ws.bitget.com/v2/ws/private"

_LIQUIDITY_MAP: dict[str, Literal["TAKER", "MAKER"]] = {"T": "TAKER", "M": "MAKER"}


class PrivateWsInboxClient(SignedRequestClient, Protocol):
    """Only the 3 API key fields this module needs for login signing --
    unlike `subscribe_order_stream`'s `_PrivateWsClient`, no REST
    resync methods are required (this leaf only handles fill events,
    see module docstring)."""

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
    """A row is only treated as a fill when `tradeId` is present and both
    price and quantity are populated (module docstring #10 U3). If any
    is missing, this row is a plain status update (e.g. live->cancelled)
    rather than a fill -- returns `None`, but the caller treats that as
    "not a fill", not "discard" (the event itself still goes to the
    inbox)."""
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
    liquidity: Literal["MAKER", "TAKER", "UNKNOWN"] = _LIQUIDITY_MAP.get(
        str(row.get("tradeScope", "")).upper(), "UNKNOWN"
    )
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
        liquidity=liquidity,
        venue_ts=_parse_bitget_timestamp(row.get("uTime") or row.get("cTime")),
    )


def parse_private_order_row(row: dict[str, Any], *, received_at: datetime) -> ProviderOrderEvent:
    """One raw `orders` channel row -> `ProviderOrderEvent`. Never raises
    or returns `None` (module docstring "no silent drops") -- if there
    is no identifier at all, an auditable row is still built keyed by a
    raw_hash-based provider_event_id."""
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
        # No order id at all -- still leave an auditable row (fail-closed).
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
    """Subscribe to the private `orders` channel and stream fill/status
    events into the inbox. `client` only needs the 3 signing fields
    (`_api_key`/`_api_secret`/`_api_passphrase`), so a `BitgetAdapter`
    instance can be passed directly (why this isn't a mixin: touching
    `adapter.py`'s inheritance list is out of this leaf's file scope --
    same principle as the decision "wiring.py changes are limited to one
    subscription-registration block", so this function stays standalone
    too). Reconnect/heartbeat/ack/seq-gap handling is entirely delegated
    to `_run_ws_subscription` (-> the shared `WsSession`) -- this
    function only assembles the login message and does the parsing."""

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
