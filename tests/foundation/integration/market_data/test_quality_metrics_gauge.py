"""LA-18 quality_metrics 게이지 음성 테스트 — 실 DB(TEST_DATABASE_URL).

Focus: gauge behavior edge cases for `export_quality_metrics`.
These negative tests complement the main integration tests in
`test_quality_metrics.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.market_data.application.quality_metrics import export_quality_metrics
from tests.foundation.integration.market_data.quality_metrics_helpers import (
    _candle,
    _clock,
    _ingest,
    _listed_instrument,
)


async def test_clock_before_candle_time_yields_negative_staleness_int_truncated(deps):
    """음성: clock()이 캔들의 last_open_time보다 과거이면 `staleness_s`가
    음수가 된다 — `_export_one`이 `int((now - last_open_time).total_seconds())`를
    정수 변환할 때 음수가 제대로 나오는지 확인한다(실제 운영에서는 발생하지
    않지만 clock mocking 시 경로 커버리지 보장)."""
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(UTC).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=1), at=t0)

    clock_before = t0 - timedelta(minutes=5)
    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(clock_before),
    )
    mine = [m for m in results if m.key.instrument_id == instrument.instrument_id]
    assert len(mine) == 1
    assert mine[0].staleness_s < 0, "과거 시점 clock 은 음수 staleness_s를 산출한다"

    gauge = registry.gauge("md_staleness_seconds", ("venue", "instrument_id", "timeframe"))
    sample = gauge.samples()[("BITGET", str(instrument.instrument_id), "1m")]
    assert sample == float(mine[0].staleness_s)


async def test_fresh_candle_yields_zero_staleness_gauge_sample(deps):
    """음성: 캔들이 '현재 시간'과 완전히 일치하는 경우 staleness_s 가 0이고
    게이지 샘플도 0.0이어야 한다 — `_export_one`이 `now - last_open_time`을
    정수 변환할 때 0이 제대로 나오는지 확인한다."""
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(UTC).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=1), at=t0)

    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(t0),  # last_open_time == t0 -> staleness_s == 0
    )
    mine = [m for m in results if m.key.instrument_id == instrument.instrument_id]
    assert len(mine) == 1
    assert mine[0].staleness_s == 0, "현재 시간과 일치하면 staleness_s는 0이다"

    gauge = registry.gauge("md_staleness_seconds", ("venue", "instrument_id", "timeframe"))
    sample = gauge.samples()[("BITGET", str(instrument.instrument_id), "1m")]
    assert sample == 0.0, "게이지 샘플도 0.0이어야 한다"


async def test_multiple_instruments_gauge_keys_do_not_collide(deps):
    """음성: 두 심볼이 같은 (venue, timeframe) 라벨을 공유하지만
    instrument_id 가 다르므로 게이지 샘플 키가 충돌하지 않는다 —
    첫 번째 심볼의 게이지가 두 번째 심볼에 의해 덮어써지지 않는다."""
    inst_a = await _listed_instrument(deps)
    inst_b = await _listed_instrument(deps)
    t0 = datetime.now(UTC).replace(second=0, microsecond=0)
    for inst in (inst_a, inst_b):
        candles = [_candle(t0, "100", "110", "90", "105", "10")]
        await _ingest(deps, inst, candles, start=t0, end=t0 + timedelta(minutes=1), at=t0)

    later = t0 + timedelta(minutes=5)
    registry = MetricsRegistry()
    await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(later),
    )

    gauge = registry.gauge("md_staleness_seconds", ("venue", "instrument_id", "timeframe"))
    samples = gauge.samples()
    key_a = ("BITGET", str(inst_a.instrument_id), "1m")
    key_b = ("BITGET", str(inst_b.instrument_id), "1m")
    assert key_a in samples, "첫 번째 심볼 게이지가 존재해야 한다"
    assert key_b in samples, "두 번째 심볼 게이지도 존재해야 한다"
    assert samples[key_a] == samples[key_b], (
        "두 심볼은 같은 staleness여야 하지만 키가 충돌하지 않는다"
    )
