"""RD-19 — OKX 공개 WS 호가(books) 파서.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. `exchanges/common/ws_session.WsSession`(재사용)에 꽂는 얇은 파서.

미검증(외부 문서 라이브 대조 전, 성공으로 위장하지 않음):
- `books` 채널의 `action: "snapshot"|"update"` 및 `seqId`/`prevSeqId`
  필드명, 구독 ack `{"event":"subscribe","arg":{...}}` 형태는 공개 문서
  기억 기반이며 라이브 대조하지 않았다. checksum 검증(문서상 CRC32)은
  스콥 밖 — `seqId` 연속성만으로 갭을 판정한다(WsSession 공용 계약).
- REST 스냅샷(`/api/v5/market/books`) `sz` 상한(문서상 400)은 대조하지
  않았다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from src.exchanges.common.ws_session import NOT_ACK, AckResult
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot

__all__ = ["OkxL2Adapter"]

_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
_REST_BASE = "https://www.okx.com"
_SZ = 400


def _levels(raw: list[list[str]]) -> tuple[tuple[Decimal, Decimal], ...]:
    # OKX 레벨은 [price, size, deprecated, numOrders] — 앞 2개만 쓴다.
    return tuple((Decimal(level[0]), Decimal(level[1])) for level in raw)


def _to_inst_id(instrument_symbol: str) -> str:
    """`BTCUSDT` → `BTC-USDT`(OKX instId 표기, base/quote 4자 이상 심볼은
    미검증 — 이 리프는 `<BASE><QUOTE>`가 quote=USDT/USDC/KRW로 끝난다고
    가정하지 않고 호출자가 이미 `BTC-USDT` 형태로 넘긴다고 가정한다)."""
    return instrument_symbol if "-" in instrument_symbol else instrument_symbol


class OkxL2Adapter:
    venue = Venue.OKX

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(base_url=_REST_BASE, timeout=10.0)

    def ws_url(self, instrument_symbol: str) -> str:
        del instrument_symbol  # 단일 public 엔드포인트는 심볼 무관
        return _WS_URL

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        inst_id = _to_inst_id(instrument_symbol)
        return [{"op": "subscribe", "args": [{"channel": "books", "instId": inst_id}]}]

    def ack_validator(self, message: dict[str, Any]) -> AckResult:
        event = message.get("event")
        if event == "subscribe":
            return AckResult(is_ack=True, ok=True, detail="subscribe")
        if event == "error":
            return AckResult(is_ack=True, ok=False, detail=str(message.get("msg", "")))
        return NOT_ACK

    def _first_row(self, message: dict[str, Any]) -> dict[str, Any] | None:
        data = message.get("data")
        if message.get("arg") is None or not isinstance(data, list) or not data:
            return None
        row = data[0]
        return row if isinstance(row, dict) else None

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        row = self._first_row(message)
        if row is None:
            return None
        value = row.get("seqId")
        return int(value) if value is not None else None

    def parse_event(self, message: dict[str, Any]) -> L2Diff | None:
        row = self._first_row(message)
        if row is None:
            return None
        as_of = datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc)
        return L2Diff(
            sequence=int(row["seqId"]),
            as_of=as_of,
            bid_updates=_levels(row.get("bids", [])),
            ask_updates=_levels(row.get("asks", [])),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        inst_id = _to_inst_id(instrument_symbol)
        resp = await self._http.get(
            "/api/v5/market/books", params={"instId": inst_id, "sz": _SZ}
        )
        resp.raise_for_status()
        row = resp.json()["data"][0]
        bids, asks = _levels(row["bids"]), _levels(row["asks"])
        return L2Snapshot(
            sequence=int(row["seqId"]),
            as_of=datetime.now(timezone.utc),
            bids=dict(bids),
            asks=dict(asks),
        )
