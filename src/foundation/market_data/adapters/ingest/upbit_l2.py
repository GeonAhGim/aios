"""RD-19 — Upbit public WS orderbook parser.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. A thin parser plugged into `exchanges/common/ws_session.WsSession` (reused).

Unlike Binance/Bybit/OKX, Upbit's public orderbook channel does **not
send incremental diffs — it resends the entire orderbook on every
tick** (based on documentation memory, unverified) — so this adapter has
no sequence field (`seq_extractor` always returns `None`) and there is no
subscription ack frame either (`ack_validator` always returns `NOT_ACK` —
the first data frame effectively serves as the ack). `parse_event`
returns an `L2Snapshot` every time (never an `L2Diff`) — when the
orchestrator receives it, it fully replaces the local state instead of
applying a diff.

Sequence-gap detection does not apply to this venue (every frame is
already the complete truth, so there is no concept of "missing" data) —
`WsSession`'s distrust-based resync on connection drop -> reconnect still
works as normal (a transport-layer disconnect is unrelated to the venue
protocol).

Unverified: the subscription frame format (`[{"ticket": ...}, {"type":
"orderbook", "codes": [...]}]`) and the response field name
(`orderbook_units`) are based on public documentation memory and have not
been checked against a live feed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from src.exchanges.common.ws_session import NOT_ACK, AckResult
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.l2_orderbook import L2Snapshot

__all__ = ["UpbitL2Adapter"]

_WS_URL = "wss://api.upbit.com/websocket/v1"
_REST_BASE = "https://api.upbit.com"


def _to_market(instrument_symbol: str) -> str:
    """Expects `KRW-BTC` notation as-is (Upbit market code) — no conversion is done."""
    return instrument_symbol


def _levels_from_units(
    units: list[dict[str, Any]],
) -> tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]:
    bids: dict[Decimal, Decimal] = {}
    asks: dict[Decimal, Decimal] = {}
    for unit in units:
        bids[Decimal(str(unit["bid_price"]))] = Decimal(str(unit["bid_size"]))
        asks[Decimal(str(unit["ask_price"]))] = Decimal(str(unit["ask_size"]))
    return bids, asks


class UpbitL2Adapter:
    venue = Venue.UPBIT

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(base_url=_REST_BASE, timeout=10.0)

    def ws_url(self, instrument_symbol: str) -> str:
        del instrument_symbol  # a single public endpoint is symbol-agnostic
        return _WS_URL

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        market = _to_market(instrument_symbol)
        return [
            {"ticket": str(uuid.uuid4())},
            {"type": "orderbook", "codes": [market]},
        ]

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        del message  # Upbit has no subscription ack frame — the data frame itself confirms it
        return NOT_ACK

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        del message  # every frame is a full snapshot — there is no sequence-gap concept
        return None

    def parse_event(self, message: dict[str, Any]) -> L2Snapshot | None:
        if message.get("type") != "orderbook":
            return None
        units = message.get("orderbook_units", [])
        bids, asks = _levels_from_units(units)
        timestamp_ms = message.get("timestamp")
        as_of = (
            datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            if timestamp_ms is not None
            else datetime.now(timezone.utc)
        )
        return L2Snapshot(sequence=0, as_of=as_of, bids=bids, asks=asks)

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        market = _to_market(instrument_symbol)
        resp = await self._http.get("/v1/orderbook", params={"markets": market})
        resp.raise_for_status()
        row = resp.json()[0]
        bids, asks = _levels_from_units(row["orderbook_units"])
        return L2Snapshot(sequence=0, as_of=datetime.now(timezone.utc), bids=bids, asks=asks)
