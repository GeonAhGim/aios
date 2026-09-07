"""R-28 — execution_loop/candle_history.py

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-28 (§2 표).
DC-28 follow-up (ADR-2026-09-06-H D7) — enforces `owner_id` in the cache key.

A pure cache layer that reuses per-symbol daily (1d) lookback candles for a
60-second TTL. The caller injects the adapter, and this class never
performs I/O directly — it only calls `adapter.get_ohlcv()` (standard 103,
the adapter-injection principle).

D7: exactly because this cache is designed to be reused (multiple calls
share the same entry during the TTL), if `execution_loop` ever shares one
instance across multiple executions (= multiple users), candles fetched via
user A's KIS connection (their brokerage account's quote entitlement) could
be handed straight to user B's request — keying by symbol alone erases
"which connection was this data fetched with". That's why `get()`'s
`owner_id` is a required keyword argument with no default: if a new caller
omits it, mypy catches it before runtime (a missing required argument is a
static error) — this file enforces D7's DoD, "an attempt to use the shared
cache without tagging is blocked by static checking", via type checking.
A public feed with no owner distinction, like Bitget, explicitly passes
`owner_id=None` — not an omission, but a declaration of "public data".
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from src.data.models.market_data import Candle
from src.exchanges.common.adapter import ExchangeAdapter

_TIMEFRAME = "1d"
_TTL_SECONDS = 60.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _CacheEntry:
    __slots__ = ("candles", "bars", "fetched_at")

    def __init__(self, candles: list[Candle], bars: int, fetched_at: datetime) -> None:
        self.candles = candles
        self.bars = bars
        self.fetched_at = fetched_at


class CandleHistoryCache:
    """Daily lookback cache keyed by (exchange, symbol, owner_id).

    TTL 60초 경과 시 재조회한다. 캐시된 bars보다 큰 bars를 요청하면
    TTL 이내여도 캐시를 재사용하지 않고 재조회한다(더 긴 lookback을
    짧은 캐시로 채울 수 없으므로). 어댑터 조회가 실패하면 만료된(stale)
    캐시를 대신 돌려주지 않고 None을 반환한다 — 호출자가 "판단 불가"로
    다뤄야 한다(var_estimator.py의 데이터 부족 None 관례와 동일).

    Because `owner_id` is part of the key, users A and B never share a
    cache entry even when querying the same symbol (D7 "statically blocked
    from cross-user response assembly").
    """

    def __init__(self, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._now = now
        self._entries: dict[tuple[str, str, UUID | None], _CacheEntry] = {}

    async def get(
        self, adapter: ExchangeAdapter, symbol: str, *, bars: int, owner_id: UUID | None
    ) -> list[Candle] | None:
        key = (adapter.get_capabilities().exchange_name, symbol, owner_id)
        now = self._now()
        entry = self._entries.get(key)
        if entry is not None and entry.bars >= bars:
            age_seconds = (now - entry.fetched_at).total_seconds()
            if age_seconds < _TTL_SECONDS:
                return entry.candles

        try:
            candles = await adapter.get_ohlcv(symbol, _TIMEFRAME, limit=bars)
        except Exception:  # noqa: BLE001 — 어댑터 실패는 전부 "판단 불가"(None)로 수렴
            return None

        self._entries[key] = _CacheEntry(candles=candles, bars=bars, fetched_at=now)
        return candles
