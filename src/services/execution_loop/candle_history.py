"""R-28 — execution_loop/candle_history.py

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-28 (§2 표)

심볼별 일봉(1d) lookback 캔들을 TTL 60초 동안 재사용하는 순수 캐시 계층.
어댑터는 호출자가 주입하고, 이 클래스는 직접 I/O를 만들지 않는다 —
`adapter.get_ohlcv()`만 호출한다(103번 공통 규칙, 어댑터 주입 원칙).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

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
    """(exchange, symbol)별 일봉 lookback 캐시.

    TTL 60초 경과 시 재조회한다. 캐시된 bars보다 큰 bars를 요청하면
    TTL 이내여도 캐시를 재사용하지 않고 재조회한다(더 긴 lookback을
    짧은 캐시로 채울 수 없으므로). 어댑터 조회가 실패하면 만료된(stale)
    캐시를 대신 돌려주지 않고 None을 반환한다 — 호출자가 "판단 불가"로
    다뤄야 한다(var_estimator.py의 데이터 부족 None 관례와 동일).
    """

    def __init__(self, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._now = now
        self._entries: dict[tuple[str, str], _CacheEntry] = {}

    async def get(
        self, adapter: ExchangeAdapter, symbol: str, *, bars: int
    ) -> list[Candle] | None:
        key = (adapter.get_capabilities().exchange_name, symbol)
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
