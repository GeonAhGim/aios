"""RD-19 — Upbit 공개 WS 호가(orderbook) 파서.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. `exchanges/common/ws_session.WsSession`(재사용)에 꽂는 얇은 파서.

Upbit의 공개 orderbook 채널은 Binance/Bybit/OKX와 달리 **증분이 아니라
매 틱 전체 호가창을 다시 보낸다**(문서 기억 기반, 미검증) — 그래서 이
어댑터는 시퀀스 필드를 갖지 않고(`seq_extractor`는 항상 `None`), 구독
ack 프레임도 없다(`ack_validator`는 항상 `NOT_ACK` — 첫 데이터 프레임이
곧 ack 역할). `parse_event`는 매번 `L2Snapshot`을 반환한다(`L2Diff`가
아님) — 오케스트레이터가 이를 받으면 로컬 상태를 diff 적용이 아니라
전체 치환한다.

시퀀스 갭 판정은 이 벤처에는 적용되지 않는다(매 프레임이 이미 완전한
진실이라 "누락"이라는 개념 자체가 없다) — 연결 끊김→재연결 시
`WsSession`의 distrust 기반 재동기화는 여전히 동작한다(전송 계층
끊김은 벤처 프로토콜과 무관).

미검증: 구독 프레임 포맷(`[{"ticket": ...}, {"type": "orderbook",
"codes": [...]}]`)과 응답 필드명(`orderbook_units`)은 공개 문서 기억
기반이며 라이브 대조하지 않았다.
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
    """`KRW-BTC` 표기를 그대로 기대한다(Upbit market 코드) — 변환하지 않는다."""
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
        del instrument_symbol  # 단일 public 엔드포인트는 심볼 무관
        return _WS_URL

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        market = _to_market(instrument_symbol)
        return [
            {"ticket": str(uuid.uuid4())},
            {"type": "orderbook", "codes": [market]},
        ]

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        del message  # Upbit는 구독 ack 프레임이 없다 — 데이터 프레임이 곧 확인
        return NOT_ACK

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        del message  # 매 프레임이 전체 스냅샷 — 시퀀스 갭 개념이 없다
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
