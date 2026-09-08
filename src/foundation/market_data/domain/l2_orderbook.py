"""RD-19 — L2 order book state update (pure).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3, D5, task-1766.

Parsing exchange WS messages (`adapters/ingest/*_l2.py`) and sequence-gap
detection / resync calls (`exchanges/common/ws_session.py`, reused — not
reimplemented) are not this module's responsibility. This module only
provides pure value objects that update local order book state from
already-parsed snapshots/diffs — no I/O, no clock (`as_of` is supplied by
the caller).

Diff application rule: a level with `qty == 0` is removed, otherwise it is
upserted (common exchange convention). Negative quantities are an exchange
protocol violation, so instead of silently ignoring them this surfaces a
`NegativeQuantityError` (fail-closed).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = [
    "NegativeQuantityError",
    "L2Snapshot",
    "L2Diff",
    "OrderBookState",
]


class NegativeQuantityError(ValueError):
    """A negative-quantity order book level.

    An exchange protocol violation; not silently ignored.
    """


@dataclass(frozen=True)
class L2Snapshot:
    """A full order book snapshot (e.g. REST resync, Upbit's full push on every tick)."""

    sequence: int
    as_of: datetime
    bids: Mapping[Decimal, Decimal]
    asks: Mapping[Decimal, Decimal]


@dataclass(frozen=True)
class L2Diff:
    """An incremental update (price -> quantity).

    `sequence` is the final sequence number after applying this diff.
    """

    sequence: int
    as_of: datetime
    bid_updates: tuple[tuple[Decimal, Decimal], ...]
    ask_updates: tuple[tuple[Decimal, Decimal], ...]


def _validate_snapshot_side(levels: Mapping[Decimal, Decimal]) -> None:
    if any(qty < 0 for qty in levels.values()):
        raise NegativeQuantityError("스냅샷 호가 수량은 0 이상이어야 한다")


def _validate_diff_side(updates: tuple[tuple[Decimal, Decimal], ...]) -> None:
    if any(qty < 0 for _, qty in updates):
        raise NegativeQuantityError("증분 호가 수량은 0 이상이어야 한다")


def _apply_side(
    side: dict[Decimal, Decimal], updates: tuple[tuple[Decimal, Decimal], ...]
) -> dict[Decimal, Decimal]:
    result = dict(side)
    for price, qty in updates:
        if qty == 0:
            result.pop(price, None)
        else:
            result[price] = qty
    return result


@dataclass(frozen=True)
class OrderBookState:
    """Local order book state at a point in time.

    Immutable — updates always return a new instance.
    """

    sequence: int
    as_of: datetime
    bids: Mapping[Decimal, Decimal]
    asks: Mapping[Decimal, Decimal]

    @staticmethod
    def from_snapshot(snapshot: L2Snapshot) -> OrderBookState:
        _validate_snapshot_side(snapshot.bids)
        _validate_snapshot_side(snapshot.asks)
        return OrderBookState(
            sequence=snapshot.sequence,
            as_of=snapshot.as_of,
            bids=dict(snapshot.bids),
            asks=dict(snapshot.asks),
        )

    def apply_diff(self, diff: L2Diff) -> OrderBookState:
        _validate_diff_side(diff.bid_updates)
        _validate_diff_side(diff.ask_updates)
        return OrderBookState(
            sequence=diff.sequence,
            as_of=diff.as_of,
            bids=_apply_side(dict(self.bids), diff.bid_updates),
            asks=_apply_side(dict(self.asks), diff.ask_updates),
        )

    def top_n(self, n: int) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        """Top-N levels for long-term-retention downsampling.

        bids sorted descending by price, asks ascending.
        """
        top_bids = sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)[:n]
        top_asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n]
        return top_bids, top_asks
