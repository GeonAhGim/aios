"""LB-14 — 캔들 기반 마크가격 소스(adapters/candle_mark_price_source.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-14.

A(`market_data`)의 최신 1분봉 종가를 마크가격으로 쓴다. 스테일 판정은
새로 만들지 않고 LA-5 `stale_detector.detect_stale`(k×duration 임계, k=3
기본값)을 그대로 재사용한다(task-654 decision) — 마크가 없거나 스테일
하면 `0`이나 직전값으로 채우지 않고 `None`을 반환한다(포트 계약,
`ports/mark_price_source.py` docstring).

`PositionKey.instrument_id`(`venue:instrument_id:strategy_id:execution_id`
의 두 번째 필드)가 실제로 market_data 참조데이터의 어떤 별칭
(`md_symbol_alias.alias_symbol`)과 일치하는지는 이 리프가 보장하지
않는다 — `ReferenceRepository.get_instrument`가 `None`을 돌려주면(등록
안 됨/형식 불일치 포함) 마크도 그냥 `None`이다(§9 LA-17 `get_candles.py`
모듈독스트링의 근사치 취급과 같은 정신).

`venue`가 `market_data.Venue`(BITGET/KIS_KRX/KIS_US)가 아니면(예:
paper/backtest 전용 venue 문자열) 조회 자체를 시도하지 않고 `None` —
아직 캔들 소스가 없는 venue라는 뜻이지 오류가 아니다.
"""
from __future__ import annotations

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Currency, Money
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.quality.stale_detector import detect_stale
from src.foundation.market_data.domain.timeframe import duration
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import ReferenceRepository
from src.foundation.positions.domain.position_key import PositionKey

__all__ = ["CandleMarkPriceSource"]

_STALE_K = 3  # LA-5 stale_detector 기본값과 동일(task-654 decision).


class CandleMarkPriceSource:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        store: CandleStore,
        refs: ReferenceRepository,
        cal: CalendarRepository,
        timeframe: Timeframe = Timeframe.M1,
    ) -> None:
        self._pool = pool
        self._store = store
        self._refs = refs
        self._cal = cal
        self._timeframe = timeframe

    async def mark(self, position_key: str, at: AwareDatetime) -> Money | None:
        key = PositionKey.parse(position_key)
        try:
            venue = Venue(key.venue.upper())
        except ValueError:
            return None

        async with self._pool.acquire() as conn:
            instrument = await self._refs.get_instrument(conn, venue, key.instrument_id, at)
            if instrument is None or instrument.quote is None:
                return None
            try:
                currency = Currency(instrument.quote)
            except ValueError:
                return None

            series_key = SeriesKey(
                venue=venue, instrument_id=instrument.instrument_id, timeframe=self._timeframe
            )
            step = duration(self._timeframe)
            last_open = await self._store.last_open_time(conn, series_key)
            if last_open is None:
                return None
            candles = await self._store.query(
                conn, series_key, last_open, last_open + step, None
            )
            if not candles:
                return None
            candle = candles[-1]

            session_open = await self._is_session_open(conn, venue, at)

        if detect_stale(candle.open_time, at, self._timeframe, session_open, k=_STALE_K):
            return None
        return Money(amount=candle.close, currency=currency)

    async def _is_session_open(
        self, conn: asyncpg.Connection, venue: Venue, at: AwareDatetime
    ) -> bool:
        spec = KNOWN_SESSIONS[venue.value]
        if spec.continuous:
            return True
        calendar = await self._cal.load(conn, venue, at.astimezone(spec.tz).year)
        return calendar.is_open(at)
