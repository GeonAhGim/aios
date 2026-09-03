"""LA-15 — Bitget 캔들 소스 어댑터(`IngestSource`, LA-9 포트 구현).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-15, §10 R8.

`ExchangeAdapter.get_ohlcv`(`src/exchanges/common/adapter.py`)만 호출한다 —
Bitget 전용 페이지네이션 메서드(`get_history_candles`)는 이 추상 인터페이스에
없으므로 쓰지 않는다(모듈 표 §2.2 LA-15 의존 = `adapter.py` 하나). `get_ohlcv`
는 `start`/`end` 파라미터가 없어(가장 최근 `limit`개만 반환) 응답을
`[start, end)` 범위로 클라이언트 측 필터링한다 — 요청 구간이 거래소가 실제로
반환하는 최신 구간보다 과거이면 빈 리스트가 될 수 있다(**미검증**: 서버
페이지네이션·시간 오프셋, §10 R8).

`limit`은 요청 구간에 필요한 캔들 수(+1 여유)로 계산하되 §10 R8 "최대
200으로 보수적으로 설정" 상한을 절대 넘지 않는다(실측 없이 도입하는 값이라
보수적으로 잡는다).

`raw_symbol`(포트 파라미터, venue 원시 심볼 — 예: "BTCUSDT")을 받아 이
어댑터 내부에서만 `symbol_normalizer.to_canonical`로 "BTC/USDT" 형식으로
바꿔 `get_ohlcv`에 넘긴다(`BitgetMarketDataMixin.get_ohlcv`가 내부적으로
`to_bitget_symbol`을 다시 거는 것과 대칭 — LA-7 규칙을 재구현하지 않고
그대로 위임).

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

__all__ = ["BitgetIngestSource", "UnsupportedVenueError"]

_MAX_LIMIT = 200  # §10 R8 미확인 — 보수적 상한, 실측 후 조정
_PLACEHOLDER_INSTRUMENT_ID = UUID(int=0)


class UnsupportedVenueError(ValueError):
    """`BitgetIngestSource`는 `Venue.BITGET`만 지원한다."""


def _to_candle_record(candle: Candle, tf: Timeframe) -> CandleRecord:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=_PLACEHOLDER_INSTRUMENT_ID, timeframe=tf)
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


class BitgetIngestSource:
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
        if venue is not Venue.BITGET:
            raise UnsupportedVenueError(f"BitgetIngestSource는 BITGET 전용: {venue!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("fetch_candles는 tz-aware datetime만 받는다")

        canonical = to_canonical(venue, raw_symbol)
        limit = _limit_for_range(start, end, tf, self._max_limit)
        raw = await self._adapter.get_ohlcv(canonical, tf.value, limit=limit)
        return [
            _to_candle_record(candle, tf) for candle in raw if start <= candle.open_time < end
        ]
