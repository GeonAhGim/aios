"""RD-19 — Exchange L2 orderbook ingestion orchestrator.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3/D5, task-1766 decision ("the session layer must reuse
`exchanges/common/ws_session.py` and must not introduce a new session
abstraction").

Plugs a venue-specific `VenueL2Adapter` (parsing only differs) into
`WsSession` (heartbeat/ack/sequence-gap/reconnect resync — reused, not
reimplemented), updates the local orderbook state
(`domain/l2_orderbook.OrderBookState`), and records the spans during which
ingestion continued without interruption into `coverage_spans`
(DC-5/DC-8, `ports/coverage_repository`).

On a gap (whether a sequence discontinuity or a connection drop), we do
not silently interpolate: the currently open coverage span is closed at
the moment the gap is detected, and a new span is opened once resync via
REST snapshot completes. A blank interval remains between the two spans —
no span is recorded for it (same spirit as §4.1 "filling out-of-coverage
intervals with 0/NaN is forbidden": leave it empty rather than fill it in,
so the loss is visible as-is).

This class's behavior given the `WsSession` callback order (source:
`ws_session.py::run`):
- Sequence gap (connection stays up): only `on_resync()` is called ->
  closes the open span and opens a new one after resync.
- Connection drop -> reconnect: `on_distrust(True)` (closes the span at
  the moment of disconnection) -> after successful reconnection,
  `on_resync()` (opens a new span after resync) ->
  `on_distrust(False)` (a span is already open, so this does nothing).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import asyncpg

from src.exchanges.common.ws_session import WsSession
from src.foundation.market_data.adapters.ingest.protocol import VenueL2Adapter
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot, OrderBookState
from src.foundation.market_data.ports.coverage_repository import (
    CoverageQuality,
    CoverageRepository,
    CoverageSpan,
)

logger = logging.getLogger(__name__)

__all__ = ["L2IngestSession"]

ClockFn = Callable[[], datetime]
OrderBookObserver = Callable[[OrderBookState], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class L2IngestSession:
    def __init__(
        self,
        *,
        adapter: VenueL2Adapter,
        instrument_id: str,
        instrument_symbol: str,
        pool: asyncpg.Pool,
        coverage_repo: CoverageRepository,
        clock: ClockFn = _utc_now,
        on_book_update: OrderBookObserver | None = None,
        ws_session_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._adapter = adapter
        self._instrument_id = instrument_id
        self._instrument_symbol = instrument_symbol
        self._pool = pool
        self._coverage_repo = coverage_repo
        self._clock = clock
        self._on_book_update = on_book_update
        self._book: OrderBookState | None = None
        self._span_start: datetime | None = None
        self._session = WsSession(
            adapter.ws_url(instrument_symbol),
            venue=adapter.venue.value,
            channel="l2_orderbook",
            ack_validator=adapter.ack_validator,
            seq_extractor=adapter.seq_extractor,
            on_resync=self._on_resync,
            on_distrust=self._on_distrust,
            **(ws_session_kwargs or {}),
        )

    @property
    def book(self) -> OrderBookState | None:
        return self._book

    async def run(self) -> None:
        """Fetch the initial snapshot before starting the subscription — the
        local state must already be consistent before the first diff arrives."""
        self._span_start = self._clock()
        await self._resync_book()
        subscriptions = self._adapter.subscription_messages(self._instrument_symbol)
        await self._session.run(subscriptions, self._handle_message)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        parsed = self._adapter.parse_event(message)
        if parsed is None:
            return
        if isinstance(parsed, L2Snapshot):
            self._book = OrderBookState.from_snapshot(parsed)
        elif isinstance(parsed, L2Diff):
            if self._book is None:
                raise RuntimeError(
                    "L2IngestSession: 스냅샷 이전에 증분이 도착했다 — "
                    "run()이 구독 전에 fetch_snapshot을 보장하므로 발생하면 어댑터 버그다"
                )
            self._book = self._book.apply_diff(parsed)
        else:  # pragma: no cover — Protocol contract violation (fail-closed)
            raise TypeError(f"parse_event가 알 수 없는 타입을 반환: {type(parsed)!r}")
        if self._on_book_update is not None:
            await self._on_book_update(self._book)

    async def _on_distrust(self, entered: bool) -> None:
        if entered:
            await self._close_span(self._clock())
        # entered=False: resync already ran _on_resync first and opened a new span.

    async def _on_resync(self) -> None:
        now = self._clock()
        if self._span_start is not None:
            await self._close_span(now)
        await self._resync_book()
        self._span_start = self._clock()

    async def _resync_book(self) -> None:
        snapshot = await self._adapter.fetch_snapshot(self._instrument_symbol)
        self._book = OrderBookState.from_snapshot(snapshot)

    async def _close_span(self, end_at: datetime) -> None:
        start = self._span_start
        self._span_start = None
        if start is None or end_at <= start:
            return
        span = CoverageSpan(
            instrument_id=self._instrument_id,
            venue=self._adapter.venue,
            timeframe=Timeframe.L2,
            quality=CoverageQuality.PROVISIONAL,
            start=start,
            end=end_at,
        )
        async with self._pool.acquire() as conn, conn.transaction():
            try:
                await self._coverage_repo.upsert_span(conn, span)
            except Exception:
                logger.exception(
                    "L2 커버리지 span 기록 실패(venue=%s instrument_id=%s start=%s end=%s) — "
                    "손실 구간이 조용히 사라지지 않도록 예외를 다시 던진다",
                    self._adapter.venue.value, self._instrument_id, start, end_at,
                )
                raise
