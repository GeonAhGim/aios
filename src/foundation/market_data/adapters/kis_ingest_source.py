"""LA-20 — KIS(KRX) 캔들 소스 어댑터(`IngestSource`, LA-9 포트 구현).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-20.

`ExchangeAdapter.get_ohlcv`(`src/exchanges/common/adapter.py`)만 호출한다
(bitget_ingest_source·LA-15와 동일 계약). `KISMarketDataMixin.get_ohlcv`
(`src/exchanges/kis/market_data_mixin.py`)는 KRX 일봉(1d)·분봉(1m)만
지원하고 `start`/`end` 파라미터가 없다(일봉은 1900-01-01~오늘 전체를
받아 `limit`으로 자르고, 분봉은 항상 "지금" 기준 최근 구간만 반환) —
응답을 `[start, end)` 범위로 클라이언트 측 필터링한다(bitget과 동일
전략). 서버가 실제로 몇 건까지/며칠치를 주는지는 **미검증**(§10 R8과
대칭되는 KIS 쪽 제약, 실측 없음)이라 `limit`은 보수적 상한을 넘지 않는다.

`raw_symbol`(KRX 6자리 코드)은 `symbol_normalizer.to_canonical`에 위임해
형식만 검증한다 — KRX는 venue 원시 심볼과 canonical 표현이 같아 변환은
없다(LA-7).

반환하는 `CandleRecord.key.instrument_id`는 알 수 없다(이 어댑터는 DB를
모른다, 71번 §4) — 플레이스홀더 UUID(nil)를 채운다. `ingest_candles`가
참조데이터에서 조회한 진짜 instrument_id로 무조건 다시 키를 씌우므로
호출자는 이 값에 의존하지 않는다.
"""
from __future__ import annotations

import math
from datetime import timedelta
from uuid import UUID

from pydantic import AwareDatetime

from src.data.models.market_data import Candle
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.domain.timeframe import duration

__all__ = ["KisIngestSource", "UnsupportedVenueError", "UnsupportedTimeframeError"]

_MAX_LIMIT = 100  # 미검증 — 실측 없이 도입하는 보수적 상한, 실측 후 조정
_PLACEHOLDER_INSTRUMENT_ID = UUID(int=0)
_SUPPORTED_TIMEFRAMES = frozenset({Timeframe.M1, Timeframe.D1})


class UnsupportedVenueError(ValueError):
    """`KisIngestSource`는 `Venue.KIS_KRX`만 지원한다."""


class UnsupportedTimeframeError(ValueError):
    """`KISMarketDataMixin.get_ohlcv`는 일봉(1d)·분봉(1m)만 지원한다."""


def _to_candle_record(candle: Candle, tf: Timeframe) -> CandleRecord:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=_PLACEHOLDER_INSTRUMENT_ID, timeframe=tf)
    return CandleRecord(
        key=key,
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def _limit_for_range(start: AwareDatetime, end: AwareDatetime, tf: Timeframe, cap: int) -> int:
    span = end - start
    if span <= timedelta(0):
        return 1
    wanted = math.ceil(span / duration(tf)) + 1
    return max(1, min(wanted, cap))


class KisIngestSource:
    def __init__(self, adapter: ExchangeAdapter, *, max_limit: int = _MAX_LIMIT) -> None:
        self._adapter = adapter
        self._max_limit = min(max_limit, _MAX_LIMIT)

    async def fetch_candles(
        self,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: AwareDatetime,
        end: AwareDatetime,
    ) -> list[CandleRecord]:
        if venue is not Venue.KIS_KRX:
            raise UnsupportedVenueError(f"KisIngestSource는 KIS_KRX 전용: {venue!r}")
        if tf not in _SUPPORTED_TIMEFRAMES:
            raise UnsupportedTimeframeError(f"KIS는 1d/1m만 지원: {tf!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("fetch_candles는 tz-aware datetime만 받는다")

        canonical = to_canonical(venue, raw_symbol)
        limit = _limit_for_range(start, end, tf, self._max_limit)
        raw = await self._adapter.get_ohlcv(canonical, tf.value, limit=limit)
        return [
            _to_candle_record(candle, tf) for candle in raw if start <= candle.open_time < end
        ]
