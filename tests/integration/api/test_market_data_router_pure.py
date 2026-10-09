"""market_data read_api.paginate_candles 순수 커서 로직 — I/O 없는 단위 테스트.

HTTP 통합 경로는 test_market_data_router.py /
test_market_data_router_entitlement.py, 공유 헬퍼는 conftest.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.foundation.market_data.application.read_api import paginate_candles
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from tests.integration.api.conftest import _candle


def test_paginate_candles_pure_cursor_semantics():
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1)
    candles = [_candle(key, t0 + timedelta(minutes=i), 100) for i in range(5)]

    page, nxt = paginate_candles(candles, None, 2)
    assert [c.open_time for c in page] == [t0, t0 + timedelta(minutes=1)]
    assert nxt == "2026-09-01T00:02:00Z"

    last, nxt2 = paginate_candles(candles, t0 + timedelta(minutes=4), 2)
    assert len(last) == 1 and nxt2 is None
    assert paginate_candles(candles, t0 + timedelta(minutes=9), 2) == ([], None)
