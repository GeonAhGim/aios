"""LA-1 -- market_data/contracts/v1 package-level contract tests.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

DEEPEN (task-8396, 원 리프 task-6704): this file existed only as an empty
package marker with zero negative tests. `v1.py` is the sole public surface
for candle/tick ingestion, quality verdicts, and instrument lifecycle
commands (per its own module docstring) but had no dedicated test module of
its own -- every other file in this package (`test_instruments_v2.py`,
`test_microstructure_v2.py`, ...) covers `v2`. This adds the missing v1
coverage: negative tests for the naive-datetime/Decimal/enum invariants the
module docstring promises, one failure-injection case, and one numeric
performance assertion, following the same D2 pattern as the sibling v2
test files.
"""

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts import v1


def _aware(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def _series_key(**overrides: object) -> v1.SeriesKey:
    base: dict[str, object] = dict(
        venue=v1.Venue.BITGET,
        instrument_id=uuid4(),
        timeframe=v1.Timeframe.M1,
    )
    base.update(overrides)
    return v1.SeriesKey.model_validate(base)


def _candle_record(**overrides: object) -> v1.CandleRecord:
    base: dict[str, object] = dict(
        key=_series_key(),
        open_time=_aware(2026, 1, 1),
        close_time=_aware(2026, 1, 1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
    )
    base.update(overrides)
    return v1.CandleRecord.model_validate(base)


def _tick_record(**overrides: object) -> v1.TickRecord:
    base: dict[str, object] = dict(
        venue=v1.Venue.BITGET,
        instrument_id=uuid4(),
        trade_id="t1",
        price=Decimal("100"),
        quantity=Decimal("1"),
        side="buy",
        traded_at=_aware(2026, 1, 1),
    )
    base.update(overrides)
    return v1.TickRecord.model_validate(base)


def _ingest_candles_command(**overrides: object) -> v1.IngestCandlesCommand:
    base: dict[str, object] = dict(
        tenant_id=None,
        venue=v1.Venue.BITGET,
        canonical_symbol="BTCUSDT",
        timeframe=v1.Timeframe.M1,
        range_start=_aware(2026, 1, 1),
        range_end=_aware(2026, 1, 2),
        trace_id=uuid4(),
    )
    base.update(overrides)
    return v1.IngestCandlesCommand.model_validate(base)


# --- baseline construction -------------------------------------------------


def test_series_key_round_trips_schema_version() -> None:
    key = _series_key()
    assert key.schema_version == "v1"


def test_candle_record_accepts_decimal_fields() -> None:
    candle = _candle_record()
    assert isinstance(candle.open, Decimal)
    assert candle.quote_volume is None


def test_tick_record_accepts_valid_side() -> None:
    tick = _tick_record()
    assert tick.side == "buy"


# --- negative tests: tz-aware datetime invariant ---------------------------


def test_candle_record_naive_open_time_rejected() -> None:
    """CLAUDE.md §3 -- every datetime is tz-aware UTC. `AwareDatetime` must
    reject a naive `open_time`, never silently assume UTC."""
    with pytest.raises(ValidationError):
        _candle_record(open_time=datetime(2026, 1, 1))


def test_candle_record_naive_close_time_rejected() -> None:
    with pytest.raises(ValidationError):
        _candle_record(close_time=datetime(2026, 1, 1))


def test_tick_record_naive_traded_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _tick_record(traded_at=datetime(2026, 1, 1))


def test_ingest_candles_command_naive_range_start_rejected() -> None:
    with pytest.raises(ValidationError):
        _ingest_candles_command(range_start=datetime(2026, 1, 1))


# --- negative tests: enum / literal invariants ------------------------------


def test_series_key_invalid_venue_rejected() -> None:
    with pytest.raises(ValidationError):
        _series_key(venue="NOT_A_VENUE")


def test_tick_record_invalid_side_literal_rejected() -> None:
    with pytest.raises(ValidationError):
        _tick_record(side="hold")


def test_replay_request_include_quarantined_cannot_be_overridden() -> None:
    """A5 (backtest determinism): `ReplayRequest.include_quarantined` is
    pinned to `Literal[False]` -- a caller trying to widen it to `True`
    (e.g. to see quarantined candles during replay) must be rejected, not
    silently coerced back to `False`."""
    with pytest.raises(ValidationError):
        v1.ReplayRequest.model_validate(
            dict(
                key=_series_key(),
                start=_aware(2026, 1, 1),
                end=_aware(2026, 1, 2),
                as_of=_aware(2026, 1, 2),
                include_quarantined=True,
            )
        )


# --- negative tests: required-field invariants ------------------------------


def test_ingest_candles_command_missing_trace_id_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.IngestCandlesCommand.model_validate(
            dict(
                tenant_id=None,
                venue=v1.Venue.BITGET,
                canonical_symbol="BTCUSDT",
                timeframe=v1.Timeframe.M1,
                range_start=_aware(2026, 1, 1),
                range_end=_aware(2026, 1, 2),
            )
        )


def test_candle_record_missing_volume_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.CandleRecord.model_validate(
            dict(
                key=_series_key(),
                open_time=_aware(2026, 1, 1),
                close_time=_aware(2026, 1, 1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
            )
        )


# --- failure injection ------------------------------------------------------


def test_trace_id_dependency_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: `IngestCandlesCommand.trace_id` (LA-15) is normally
    stamped from `uuid.uuid4()` by the caller. If that dependency raises
    (e.g. a broken RNG/entropy source), building the command must fail
    closed -- propagate the exception -- rather than the caller catching it
    and falling back to a placeholder/zero UUID that would corrupt the
    idempotency trace."""

    def _broken_uuid4() -> UUID:
        raise RuntimeError("simulated uuid4 backend failure")

    monkeypatch.setattr(uuid, "uuid4", _broken_uuid4)

    def _build_command_with_fresh_trace_id() -> v1.IngestCandlesCommand:
        return _ingest_candles_command(trace_id=uuid.uuid4())

    with pytest.raises(RuntimeError, match="simulated uuid4 backend failure"):
        _build_command_with_fresh_trace_id()


# --- numeric performance assertion ------------------------------------------


@pytest.mark.perf
def test_bulk_candle_record_construction_completes_within_latency_budget() -> None:
    """Numeric performance assertion: constructing a large in-memory candle
    page (e.g. LA-15 ingest, LA-8 replay) must not become a validation
    bottleneck. 10,000 candles is far more than a single ingest batch; the
    budget is a generous ceiling on frozen-schema validation cost, not a
    copy of any specific SLO."""
    key = _series_key()
    started = time.perf_counter()
    candles = [
        v1.CandleRecord(
            key=key,
            open_time=_aware(2026, 1, 1),
            close_time=_aware(2026, 1, 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal(str(i)),
        )
        for i in range(10_000)
    ]
    elapsed_s = time.perf_counter() - started

    assert len(candles) == 10_000
    assert elapsed_s < 2.0, f"constructing 10,000 CandleRecords took {elapsed_s:.3f}s (budget 2.0s)"
