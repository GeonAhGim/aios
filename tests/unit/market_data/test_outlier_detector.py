"""LA-6 — market_data/domain/quality/outlier_detector.py 스파이크 탐지 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §8.1, §9.2 LA-6.

핵심 케이스(§8.1): 합성 시계열에 +30% 스파이크 1개 → 정확히 그 캔들만
검출, 변동성 높은 정상 구간(고정 시드)은 오탐 0. 추가로 채널 2(인접 캔들
대비 high/low 비율 상한)도 별도 검증한다.
"""
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.quality.outlier_detector import detect_spikes

UTC = timezone.utc
_KEY = SeriesKey(venue=Venue.BITGET, instrument_id=UUID(int=1), timeframe=Timeframe.M1)
_START = datetime(2026, 9, 1, tzinfo=UTC)


def _series(closes: list[Decimal]) -> list[CandleRecord]:
    candles = []
    open_price = closes[0]
    for i, close in enumerate(closes):
        high = max(open_price, close) * Decimal("1.001")
        low = min(open_price, close) * Decimal("0.999")
        open_time = _START + timedelta(minutes=i)
        candles.append(
            CandleRecord(
                key=_KEY,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=Decimal("1"),
            )
        )
        open_price = close
    return candles


def _noisy_closes(seed: int, steps: int, bound: str) -> list[Decimal]:
    rnd = random.Random(seed)
    limit = float(bound)
    closes = [Decimal("100")]
    for _ in range(steps):
        factor = Decimal(str(round(1 + rnd.uniform(-limit, limit), 6)))
        closes.append(closes[-1] * factor)
    return closes


def test_detect_spikes_flags_exactly_the_injected_spike_candle() -> None:
    closes = _noisy_closes(seed=42, steps=300, bound="0.02")
    spike_at = 150
    spiked = closes[:spike_at] + [c * Decimal("1.30") for c in closes[spike_at:]]
    candles = _series(spiked)

    issues = detect_spikes(candles)

    assert len(issues) == 1
    assert issues[0].type is QualityIssueType.SPIKE
    assert issues[0].open_time == candles[spike_at].open_time


def test_detect_spikes_no_false_positive_on_volatile_normal_segment() -> None:
    closes = _noisy_closes(seed=7, steps=300, bound="0.03")
    candles = _series(closes)

    issues = detect_spikes(candles)

    assert issues == []


def test_detect_spikes_flags_high_low_ratio_channel() -> None:
    flat = [Decimal("100")] * 80
    candles = _series(flat)
    wick_idx = 40
    original = candles[wick_idx]
    candles[wick_idx] = original.model_copy(update={"high": original.high * Decimal("4")})

    issues = detect_spikes(candles)

    assert len(issues) == 1
    assert issues[0].open_time == candles[wick_idx].open_time
    assert issues[0].detail["reason"] == "hl_ratio"


def test_detect_spikes_fewer_than_two_candles_returns_empty() -> None:
    candles = _series([Decimal("100")])

    assert detect_spikes(candles) == []
