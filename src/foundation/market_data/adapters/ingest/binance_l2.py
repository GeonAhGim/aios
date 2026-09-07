"""RD-19 — Binance 공개 WS 증분 호가(depthUpdate) 파서.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. `exchanges/common/ws_session.WsSession`(재사용)에 꽂는 얇은 파서 —
새 세션 계층을 만들지 않는다.

미검증(외부 문서 라이브 대조 전, 성공으로 위장하지 않음):
- combined stream 엔드포인트(`wss://stream.binance.com:9443/ws`)에 대한
  SUBSCRIBE 프레임 응답이 `{"result": null, "id": ...}` 형태라는 것은
  공개 문서 기억 기반이며 실제 응답을 라이브로 대조하지 않았다.
- REST 스냅샷 엔드포인트(`/api/v3/depth`)의 요청당 rate limit(weight)은
  대조하지 않았다 — 이 리프는 rate limit 헤더 파싱을 하지 않는다.
- 엄밀한 시퀀스 연속성 규칙은 문서상 `U <= last_u + 1 <= u`이지만,
  `WsSession.seq_extractor` 계약은 메시지당 정수 1개만 받으므로 최종
  갱신ID(`u`)만 비교하는 근사치를 쓴다(엄밀한 `U` 검증은 스콥 밖).
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
        del instrument_symbol  # combined stream 엔드포인트는 심볼 무관
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
