"""LA-8 — market_data/domain/lineage.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8,
`unit/market_data/test_lineage.py` 표(§9행 570): 순서 다른 같은 레코드 → 같은
해시, 한 값 변경 → 다른 해시.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.lineage import batch_hash, request_fingerprint


def _candle(open_time: datetime, close: str) -> CandleRecord:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.D1)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def test_batch_hash_is_order_independent() -> None:
    a = _candle(datetime(2024, 1, 1, tzinfo=timezone.utc), "100")
    b = _candle(datetime(2024, 1, 2, tzinfo=timezone.utc), "200")

    assert batch_hash([a, b]) == batch_hash([b, a])


def test_batch_hash_changes_when_one_value_changes() -> None:
    a = _candle(datetime(2024, 1, 1, tzinfo=timezone.utc), "100")
    b = _candle(datetime(2024, 1, 2, tzinfo=timezone.utc), "200")
    b_changed = _candle(datetime(2024, 1, 2, tzinfo=timezone.utc), "201")

    assert batch_hash([a, b]) != batch_hash([a, b_changed])


def test_batch_hash_empty_is_stable() -> None:
    assert batch_hash([]) == batch_hash([])


def test_request_fingerprint_is_deterministic_regardless_of_param_order() -> None:
    fp1 = request_fingerprint("kis", {"symbol": "005930", "venue": "KRX"})
    fp2 = request_fingerprint("kis", {"venue": "KRX", "symbol": "005930"})

    assert fp1 == fp2


def test_request_fingerprint_changes_when_param_value_changes() -> None:
    fp1 = request_fingerprint("kis", {"symbol": "005930"})
    fp2 = request_fingerprint("kis", {"symbol": "000660"})

    assert fp1 != fp2
