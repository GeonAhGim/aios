"""RD-19 — Binance public WS incremental orderbook (depthUpdate) parser.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. A thin parser plugged into `exchanges/common/ws_session.WsSession`
(reused) — does not create a new session layer.

Unverified (not checked live against external docs, not pretending to be
verified):
- That the SUBSCRIBE frame response for the combined stream endpoint
  (`wss://stream.binance.com:9443/ws`) has the shape `{"result": null,
  "id": ...}` is based on public documentation memory and has not been
  checked against a live response.
- The per-request rate limit (weight) of the REST snapshot endpoint
  (`/api/v3/depth`) has not been verified — this leaf does not parse rate
  limit headers.
- The strict sequence-continuity rule per the docs is `U <= last_u + 1 <=
  u`, but since the `WsSession.seq_extractor` contract only accepts a
  single integer per message, this uses the approximation of comparing
  only the final update ID (`u`) (strict `U` validation is out of scope).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from src.exchanges.common.ws_session import NOT_ACK, AckResult
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot

__all__ = ["BinanceL2Adapter"]

_WS_URL = "wss://stream.binance.com:9443/ws"
_REST_BASE = "https://api.binance.com"


def _levels(raw: list[list[str]]) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple((Decimal(price), Decimal(qty)) for price, qty in raw)


class BinanceL2Adapter:
    venue = Venue.BINANCE

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(base_url=_REST_BASE, timeout=10.0)

    def ws_url(self, instrument_symbol: str) -> str:
        del instrument_symbol  # the combined stream endpoint is symbol-agnostic
        return _WS_URL

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        stream = f"{instrument_symbol.lower()}@depth"
        return [{"method": "SUBSCRIBE", "params": [stream], "id": 1}]

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        if "id" in message and "e" not in message:
            error = message.get("error")
            return AckResult(is_ack=True, ok=error is None, detail=str(error or ""))
        return NOT_ACK

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        value = message.get("u")
        return int(value) if value is not None else None

    def parse_event(self, message: dict[str, Any]) -> L2Diff | None:
        if message.get("e") != "depthUpdate":
            return None
        as_of = datetime.fromtimestamp(message["E"] / 1000, tz=timezone.utc)
        return L2Diff(
            sequence=int(message["u"]),
            as_of=as_of,
            bid_updates=_levels(message.get("b", [])),
            ask_updates=_levels(message.get("a", [])),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        resp = await self._http.get(
            "/api/v3/depth", params={"symbol": instrument_symbol.upper(), "limit": 1000}
        )
        resp.raise_for_status()
        data = resp.json()
        return L2Snapshot(
            sequence=int(data["lastUpdateId"]),
            as_of=datetime.now(timezone.utc),
            bids={Decimal(p): Decimal(q) for p, q in data["bids"]},
            asks={Decimal(p): Decimal(q) for p, q in data["asks"]},
        )
