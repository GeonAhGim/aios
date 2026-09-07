"""RD-19 — venue별 L2 파서 계약.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3, task-1766 decision(세션 계층은 `exchanges/common/ws_session.py`를
재사용하고 새 세션 추상을 신설하지 않는다).

`ack_validator`/`seq_extractor`는 `WsSession`이 그대로 호출하는 콜백
시그니처를 맞춘 것 — 이 Protocol이 그 계약(재사용)을 강제한다. venue
어댑터는 파싱만 다르고, 재연결·하트비트·백오프·seq 갭 판정 로직은 전혀
갖지 않는다(그건 전부 `WsSession` 소관).
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
        """구독 대상 심볼의 WS 접속 URL."""
        ...

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        """연결 직후 보낼 구독 프레임(들). `WsSession._open`이 순서대로 전송한다."""
        ...

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        """`WsSession`이 매 프레임에 호출 — 구독 ack면 `is_ack=True`."""
        ...

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        """`WsSession`의 시퀀스 갭 판정용. ack·비데이터 프레임은 `None`."""
        ...

    def parse_event(self, message: dict[str, Any]) -> L2Snapshot | L2Diff | None:
        """ack가 아닌 데이터 프레임을 정규화. 무시할 프레임은 `None`."""
        ...

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        """REST 전체 스냅샷 — 최초 연결·재동기화(`on_resync`) 양쪽에서 호출."""
        ...
