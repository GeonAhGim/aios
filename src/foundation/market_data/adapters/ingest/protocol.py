"""RD-19 — Per-venue L2 parser contract.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3, task-1766 decision (the session layer must reuse
`exchanges/common/ws_session.py` and must not introduce a new session
abstraction).

`ack_validator`/`seq_extractor` match the callback signatures that
`WsSession` calls directly — this Protocol enforces that contract
(reuse). Venue adapters differ only in parsing; they have no
reconnection/heartbeat/backoff/sequence-gap-detection logic of their own
(that is entirely `WsSession`'s responsibility).
"""
from __future__ import annotations

from typing import Any, Protocol

from src.exchanges.common.ws_session import AckResult
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot

__all__ = ["VenueL2Adapter"]


class VenueL2Adapter(Protocol):
    venue: Venue

    def ws_url(self, instrument_symbol: str) -> str:
        """WS connection URL for the symbol being subscribed to."""
        ...

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        """Subscription frame(s) to send right after connecting.

        Sent in order by `WsSession._open`.
        """
        ...

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        """Called by `WsSession` for every frame — `is_ack=True` if it's a subscription ack."""
        ...

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        """Used by `WsSession` for sequence-gap detection. `None` for ack/non-data frames."""
        ...

    def parse_event(self, message: dict[str, Any]) -> L2Snapshot | L2Diff | None:
        """Normalizes a non-ack data frame. `None` for frames to ignore."""
        ...

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        """Full REST snapshot — called both on initial connect and on resync (`on_resync`)."""
        ...
