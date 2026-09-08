"""RD-19 — Bybit public WS orderbook parser.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. A thin parser plugged into `exchanges/common/ws_session.WsSession` (reused).

Unverified (not checked live against external docs, not pretending to be
verified):
- That the v5 public spot channel (`orderbook.50.<symbol>`)'s first push
  is `type: "snapshot"` and subsequent pushes are `type: "delta"`, and
  that the subscription ack has the shape `{"success": true, "op":
  "subscribe"}`, are based on public documentation memory and have not
  been checked against a live feed.
- The depth parameter upper bound for the REST snapshot
  (`/v5/market/orderbook`) (spot=200 per the docs) has not been verified,
  so a conservative value of 50 is used.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from src.exchanges.common.ws_session import NOT_ACK, AckResult
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot

__all__ = ["BybitL2Adapter"]

_WS_URL = "wss://stream.bybit.com/v5/public/spot"
_REST_BASE = "https://api.bybit.com"
_DEPTH = 50


def _levels(raw: list[list[str]]) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple((Decimal(price), Decimal(qty)) for price, qty in raw)


class BybitL2Adapter:
    venue = Venue.BYBIT

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(base_url=_REST_BASE, timeout=10.0)

    def ws_url(self, instrument_symbol: str) -> str:
        del instrument_symbol  # the single public spot endpoint is symbol-agnostic
        return _WS_URL

    def _topic(self, instrument_symbol: str) -> str:
        return f"orderbook.{_DEPTH}.{instrument_symbol.upper()}"

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        return [{"op": "subscribe", "args": [self._topic(instrument_symbol)]}]

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        if message.get("op") == "subscribe":
            ok = bool(message.get("success"))
            return AckResult(is_ack=True, ok=ok, detail=str(message.get("ret_msg", "")))
        return NOT_ACK

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        data = message.get("data")
        if not isinstance(data, dict):
            return None
        value = data.get("u")
        return int(value) if value is not None else None

    def parse_event(self, message: dict[str, Any]) -> L2Diff | None:
        data = message.get("data")
        if message.get("topic") is None or not isinstance(data, dict):
            return None
        as_of = datetime.fromtimestamp(message["ts"] / 1000, tz=timezone.utc)
        return L2Diff(
            sequence=int(data["u"]),
            as_of=as_of,
            bid_updates=_levels(data.get("b", [])),
            ask_updates=_levels(data.get("a", [])),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        resp = await self._http.get(
            "/v5/market/orderbook",
            params={"category": "spot", "symbol": instrument_symbol.upper(), "limit": _DEPTH},
        )
        resp.raise_for_status()
        result = resp.json()["result"]
        return L2Snapshot(
            sequence=int(result["u"]),
            as_of=datetime.now(timezone.utc),
            bids={Decimal(p): Decimal(q) for p, q in result["b"]},
            asks={Decimal(p): Decimal(q) for p, q in result["a"]},
        )
