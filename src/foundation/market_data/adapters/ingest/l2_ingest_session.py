"""RD-19 — 거래소 L2 호가창 수집 오케스트레이터.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3·D5, task-1766 decision("세션 계층은 `exchanges/common/ws_session.py`를
재사용하고 새 세션 추상을 신설하지 마라").

`WsSession`(하트비트·ack·시퀀스갭·재연결 재동기화 — 재사용, 재구현
아님)에 거래소별 `VenueL2Adapter`(파싱만 다름)를 꽂고, 로컬 호가창 상태
(`domain/l2_orderbook.OrderBookState`)를 갱신하며, 수집이 끊김 없이
이어진 구간을 `coverage_spans`(DC-5/DC-8, `ports/coverage_repository`)에
기록한다.

갭(시퀀스 불연속이든 연결 끊김이든) 발생 시 조용한 보간을 하지 않는다:
갭이 감지된 시각에 현재 열려 있는 커버리지 span을 그 시각에서 닫고,
REST 스냅샷으로 재동기화가 끝난 시각에 새 span을 연다. 두 span 사이에는
빈 구간이 남는다 — 그 구간에는 어떤 span도 기록하지 않는다(§4.1
"커버리지 밖 구간을 0/NaN으로 채우는 것은 금지"와 동일 정신: 채워
넣지 않고 비워 둔 채로 손실을 있는 그대로 드러낸다).

`WsSession` 콜백 순서(소스: `ws_session.py::run`)에 따른 이 클래스의
동작:
- 시퀀스 갭(연결 유지 중): `on_resync()`만 호출된다 → 열린 span을 닫고
  재동기화 후 새 span을 연다.
- 연결 끊김→재연결: `on_distrust(True)`(끊긴 시각에 span을 닫음) →
  재연결 성공 후 `on_resync()`(재동기화 후 새 span을 염) →
  `on_distrust(False)`(이미 열려 있으므로 아무 것도 하지 않음).
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
        """최초 스냅샷을 받고 나서 구독을 시작한다 — 첫 증분이 도착하기
        전에 로컬 상태가 이미 일관돼 있어야 한다."""
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
        else:  # pragma: no cover — Protocol 계약 위반(fail-closed)
            raise TypeError(f"parse_event가 알 수 없는 타입을 반환: {type(parsed)!r}")
        if self._on_book_update is not None:
            await self._on_book_update(self._book)

    async def _on_distrust(self, entered: bool) -> None:
        if entered:
            await self._close_span(self._clock())
        # entered=False: 재동기화는 _on_resync가 먼저 실행돼 이미 새 span을 열었다.

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
