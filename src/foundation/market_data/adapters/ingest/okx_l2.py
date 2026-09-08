"""RD-19 — OKX public WS orderbook (books) parser.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. A thin parser plugged into `exchanges/common/ws_session.WsSession` (reused).

Unverified (not checked live against external docs, not pretending to be
verified):
- The `books` channel's `action: "snapshot"|"update"` behavior, the
  `seqId`/`prevSeqId` field names, and the subscription ack shape
  `{"event":"subscribe","arg":{...}}` are based on public documentation
  memory and have not been checked against a live feed. Checksum
  validation (CRC32 per the docs) is out of scope — gaps are determined
  solely from `seqId` continuity (the WsSession shared contract).
- The REST snapshot (`/api/v5/market/books`) `sz` upper bound (400 per
  the docs) has not been verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from src.exchanges.common.ws_session import NOT_ACK, AckResult
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot

__all__ = ["OkxL2Adapter"]

_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
_REST_BASE = "https://www.okx.com"
_SZ = 400


def _levels(raw: list[list[str]]) -> tuple[tuple[Decimal, Decimal], ...]:
    # OKX levels are [price, size, deprecated, numOrders] — only the first 2 are used.
    return tuple((Decimal(level[0]), Decimal(level[1])) for level in raw)


def _to_inst_id(instrument_symbol: str) -> str:
    """`BTCUSDT` -> `BTC-USDT` (OKX instId notation; base/quote symbols of 4+
    characters are unverified — this leaf does not assume `<BASE><QUOTE>`
    ends with quote=USDT/USDC/KRW, and instead assumes the caller already
    passes the `BTC-USDT` form)."""
    return instrument_symbol if "-" in instrument_symbol else instrument_symbol


class OkxL2Adapter:
    venue = Venue.OKX

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(base_url=_REST_BASE, timeout=10.0)

    def ws_url(self, instrument_symbol: str) -> str:
        del instrument_symbol  # a single public endpoint is symbol-agnostic
        return _WS_URL

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        inst_id = _to_inst_id(instrument_symbol)
        return [{"op": "subscribe", "args": [{"channel": "books", "instId": inst_id}]}]

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        event = message.get("event")
        if event == "subscribe":
            return AckResult(is_ack=True, ok=True, detail="subscribe")
        if event == "error":
            return AckResult(is_ack=True, ok=False, detail=str(message.get("msg", "")))
        return NOT_ACK

    def _first_row(self, message: dict[str, Any]) -> dict[str, Any] | None:
        data = message.get("data")
        if message.get("arg") is None or not isinstance(data, list) or not data:
            return None
        row = data[0]
        return row if isinstance(row, dict) else None

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        row = self._first_row(message)
        if row is None:
            return None
        value = row.get("seqId")
        return int(value) if value is not None else None

    def parse_event(self, message: dict[str, Any]) -> L2Diff | None:
        row = self._first_row(message)
        if row is None:
            return None
        as_of = datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc)
        return L2Diff(
            sequence=int(row["seqId"]),
            as_of=as_of,
            bid_updates=_levels(row.get("bids", [])),
            ask_updates=_levels(row.get("asks", [])),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        inst_id = _to_inst_id(instrument_symbol)
        resp = await self._http.get(
            "/api/v5/market/books", params={"instId": inst_id, "sz": _SZ}
        )
        resp.raise_for_status()
        row = resp.json()["data"][0]
        bids, asks = _levels(row["bids"]), _levels(row["asks"])
        return L2Snapshot(
            sequence=int(row["seqId"]),
            as_of=datetime.now(timezone.utc),
            bids=dict(bids),
            asks=dict(asks),
        )
