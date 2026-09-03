"""LB-14 — 캔들 기반 환율 소스(adapters/fx_rate_source.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-14, R7.

**미검증(R7)**: "Bitget·KIS 참조 시세 중앙값"이 실제로 어떤
venue/instrument 조합을 가리키는지는 이 리프가 정의하지 않는다 — 서울
외국환중개 매매기준율 같은 기관 기준이 아직 채택되지 않았고(R7 "미확인"),
이 리프가 그 판단을 대신하면 검증되지 않은 사실을 코드로 확정하게 된다.
대신 호출자가 `references` 생성자 인자로 통화쌍마다 참조로 삼을
`SeriesKey` 목록을 주입한다(각 시리즈 최신 1분봉 종가가 "1 base = X
quote"를 뜻한다고 가정 — 방향이 반대인 시리즈를 잘못 넣으면 환율이
역수로 계산된다, 배선 책임은 호출자에게 있다).

스테일 판정은 `candle_mark_price_source.py`와 같은 LA-5
`detect_stale`(k=3)을 재사용한다(task-654 decision) — 개별 참조 시리즈가
스테일/누락이면 그 값만 제외하고 나머지로 중앙값을 계속 낸다. 전부
없으면 `0`으로 대체하지 않고 `None`(포트 계약,
`ports/fx_rate_source.py` docstring)."""
from __future__ import annotations

from decimal import Decimal
from statistics import median

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Currency, FXRate
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.quality.stale_detector import detect_stale
from src.foundation.market_data.domain.timeframe import duration
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore

__all__ = ["CandleFxRateSource"]

_STALE_K = 3  # LA-5 stale_detector 기본값과 동일(task-654 decision).


class CandleFxRateSource:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        store: CandleStore,
        cal: CalendarRepository,
        references: dict[tuple[Currency, Currency], list[SeriesKey]],
        timeframe: Timeframe = Timeframe.M1,
    ) -> None:
        self._pool = pool
        self._store = store
        self._cal = cal
        self._references = references
        self._timeframe = timeframe

    async def rate(self, base: Currency, quote: Currency, at: AwareDatetime) -> FXRate | None:
        keys = self._references.get((base, quote))
        invert = False
        if keys is None:
            keys = self._references.get((quote, base))
            invert = True
        if not keys:
            return None

        async with self._pool.acquire() as conn:
            legs = [leg for leg in [await self._latest_leg(conn, k, at) for k in keys] if leg]
        if not legs:
            return None

        value = median(price for price, _ in legs)
        oldest = min(ts for _, ts in legs)
        if invert:
            if value == 0:
                return None
            value = Decimal(1) / value
        return FXRate(base=base, quote=quote, rate=value, timestamp=oldest, source="candle_median")

    async def _latest_leg(
        self, conn: asyncpg.Connection, key: SeriesKey, at: AwareDatetime
    ) -> tuple[Decimal, AwareDatetime] | None:
        step = duration(self._timeframe)
        last_open = await self._store.last_open_time(conn, key)
        if last_open is None:
            return None
        candles = await self._store.query(conn, key, last_open, last_open + step, None)
        if not candles:
            return None
        candle = candles[-1]

        spec = KNOWN_SESSIONS[key.venue.value]
        session_open = (
            True
            if spec.continuous
            else (await self._cal.load(conn, key.venue, at.astimezone(spec.tz).year)).is_open(at)
        )
        if detect_stale(candle.open_time, at, self._timeframe, session_open, k=_STALE_K):
            return None
        return candle.close, candle.open_time
