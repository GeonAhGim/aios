"""NHAdapter WebSocket data-frame parsing -- pure functions (no I/O).

Spec: 02e_nh_api_spec_v1.md#S4, docs/exchanges/NH_GAPS.md#2.

2026-09-16 (task-2615) re-investigation -- the earlier session (task-114,
websocket_mixin.py module docstring) only checked the official SDK source
(`nhplug/realtime.py`), which delegates `body` field parsing to the caller,
so it left the field schema "unconfirmed". This time the asset-class
official OpenAPI spec (`https://www.nhplug.com/openapi-docs/krstock/
openapi.json`, the domain is SSOT) was downloaded directly with `curl` and
its root keys enumerated with `json.load()` -- the `x-realtime-channels`
key actually exists. The earlier WebFetch investigation was cut short
because the document is large (~450KB) and the summarizing model never
reached that section (see NH_GAPS.md for details).

Confirmed `body` fields for `x-realtime-channels.channels[]` tr_cd="mc"
(domestic stock real-time consolidated trade price, KRX+NXT), 29 fields
total, verbatim from the official spec: code, time, sign, change, price,
chrate, high, low, offer, bid, volume, volrate, movolume, value, open,
avgprice, janggubun, bidrate, volpower, new_volume, bidvolall, offvolall,
kospigb, value_won, marketgb, main_close, market_sign, market_change,
market_chrate. The 5 fields used for the `Ticker` mapping
(code/price/offer/bid/volume) are all in this list -- the remaining
fields are not used because `Ticker` has no matching slot for them (a
deliberate choice after confirming the schema, not a guess).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Ticker

_MC_TR_CD = "mc"


def parse_mc_ticker_frame(raw: str, *, exchange: str = "nh") -> Ticker | None:
    """Convert only `tr_cd="mc"` data frames into a `Ticker`.

    Subscribe-ack frames (header has `tr_type`/`rsp_cd` -- see the
    distinguishing rule in websocket_mixin.py's module docstring) and data
    frames from other channels (`mb`/`d2`, etc.) are not this parser's
    responsibility, so it returns `None` and silently ignores them. If it
    *is* an `mc` data frame but a confirmed field is missing (the server
    responded differently from the spec), it fails immediately with
    `FatalExchangeError` instead of guessing.
    """
    try:
        frame: Any = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise FatalExchangeError(
            f"NH WS frame is not JSON (x-realtime-channels confirms always-JSON): {raw!r}"
        ) from exc
    if not isinstance(frame, dict):
        raise FatalExchangeError(f"NH WS frame is not a JSON object: {raw!r}")
    header = frame.get("header")
    body = frame.get("body")
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise FatalExchangeError(f"NH WS frame is not shaped {{header, body}}: {raw!r}")
    if "tr_type" in header or "rsp_cd" in header:
        return None  # subscribe ack -- not a data frame, nothing to parse
    if header.get("tr_cd") != _MC_TR_CD:
        return None  # data from another channel -- outside this parser's scope
    try:
        return Ticker(
            symbol=str(body["code"]),
            exchange=exchange,
            price=Decimal(str(body["price"])),
            bid=Decimal(str(body["bid"])),
            ask=Decimal(str(body["offer"])),
            volume_24h=Decimal(str(body["volume"])),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )
    except KeyError as exc:
        raise FatalExchangeError(
            f"NH mc-channel frame missing expected field (official "
            f"x-realtime-channels requires code/price/offer/bid/volume, "
            f"see NH_GAPS.md S2): {exc}"
        ) from exc
