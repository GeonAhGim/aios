"""R-28 — execution_loop/candle_history.py

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-28 (§2 표).
DC-28 후속(ADR-2026-09-06-H D7) — 캐시 키에 `owner_id`를 강제한다.

심볼별 일봉(1d) lookback 캔들을 TTL 60초 동안 재사용하는 순수 캐시 계층.
어댑터는 호출자가 주입하고, 이 클래스는 직접 I/O를 만들지 않는다 —
`adapter.get_ohlcv()`만 호출한다(103번 공통 규칙, 어댑터 주입 원칙).

D7: 이 캐시가 재사용되도록 설계된 그대로(TTL 동안 여러 호출이 같은 항목을
공유) `execution_loop`가 언젠가 여러 실행(=여러 사용자)에 걸쳐 인스턴스
하나를 공유하게 되면, 사용자 A의 KIS 연결(그의 증권사 계정 시세 이용권)로
가져온 캔들이 사용자 B의 요청에 그대로 나갈 수 있다 — 심볼만으로 키를
잡으면 "누구의 연결로 가져온 데이터인가"가 사라지기 때문이다. 그래서
`get()`의 `owner_id`는 기본값 없는 필수 키워드 인자다: 새 호출부가 이
인자를 빠뜨리면 런타임 이전에 mypy가 잡는다(무형 인자 누락은 정적 오류) —
"공유 캐시에 태깅 없이 쓰려는 시도가 정적 검사에서 막힌다"(D7 DoD)를 이
파일 안에서는 타입 검사로 강제한다. Bitget처럼 소유자 구분이 없는 공개
피드는 `owner_id=None`을 명시적으로 넘긴다 — 누락이 아니라 "공개 데이터"라는
선언이다.
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
    """(exchange, symbol, owner_id)별 일봉 lookback 캐시.

    TTL 60초 경과 시 재조회한다. 캐시된 bars보다 큰 bars를 요청하면
    TTL 이내여도 캐시를 재사용하지 않고 재조회한다(더 긴 lookback을
    짧은 캐시로 채울 수 없으므로). 어댑터 조회가 실패하면 만료된(stale)
    캐시를 대신 돌려주지 않고 None을 반환한다 — 호출자가 "판단 불가"로
    다뤄야 한다(var_estimator.py의 데이터 부족 None 관례와 동일).

    `owner_id`가 키에 포함되므로 사용자 A·B가 같은 심볼을 조회해도 서로의
    캐시 항목을 절대 공유하지 않는다(D7 "교차 사용자 응답 조립에서 정적으로
    차단").
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
