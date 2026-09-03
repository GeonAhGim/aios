"""LA-1 — market_data/contracts/v1 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

`fixtures/market_data_contracts_v1.json`은 현재 스키마의 스냅샷이다. 필드를
지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드 제거 시
실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만 fixture를 함께
갱신한다.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts import v1

FIXTURE = Path(__file__).parent / "fixtures" / "market_data_contracts_v1.json"

_MODELS = (
    v1.SeriesKey,
    v1.CandleRecord,
    v1.TickRecord,
    v1.QualityIssue,
    v1.QualityVerdict,
    v1.IngestCandlesCommand,
    v1.IngestBatchResult,
    v1.TickIngestBatchResult,
    v1.CandleQuery,
    v1.CandleSeries,
    v1.ReplayRequest,
    v1.ReplaySeries,
    v1.SessionWindow,
    v1.CalendarDay,
    v1.InstrumentRef,
    v1.RegisterInstrumentCommand,
    v1.LifecycleEventCommand,
    v1.CorporateAction,
    v1.DataQualityMetrics,
)


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _sample_key(**overrides: object) -> v1.SeriesKey:
    base: dict[str, object] = dict(
        venue=v1.Venue.BITGET,
        instrument_id=uuid4(),
        timeframe=v1.Timeframe.M1,
    )
    base.update(overrides)
    return v1.SeriesKey(**base)  # type: ignore[arg-type]


def _sample_candle(**overrides: object) -> v1.CandleRecord:
    base: dict[str, object] = dict(
        key=_sample_key(),
        open_time=_now(),
        close_time=_now(),
        open=Decimal("50000"),
        high=Decimal("50100"),
        low=Decimal("49900"),
        close=Decimal("50050"),
        volume=Decimal("1.5"),
    )
    base.update(overrides)
    return v1.CandleRecord(**base)  # type: ignore[arg-type]


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_candle_record_naive_open_time_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_candle(open_time=datetime(2026, 9, 3, 0, 0))


def test_candle_record_accepts_aware_open_time() -> None:
    candle = _sample_candle()
    assert candle.open_time.tzinfo is not None
    assert candle.schema_version == "v1"


def test_candle_record_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.CandleRecord(  # type: ignore[call-arg]
            key=_sample_key(),
            open_time=_now(),
            close_time=_now(),
            open=Decimal("50000"),
            high=Decimal("50100"),
            low=Decimal("49900"),
            # close 필드 누락
            volume=Decimal("1.5"),
        )


def test_tick_record_naive_traded_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.TickRecord(
            venue=v1.Venue.BITGET,
            instrument_id=uuid4(),
            trade_id="t-1",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            side="buy",
            traded_at=datetime(2026, 9, 3, 0, 0),
        )


def test_ingest_candles_command_naive_range_start_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.IngestCandlesCommand(
            tenant_id=None,
            venue=v1.Venue.BITGET,
            canonical_symbol="BTC/USDT",
            timeframe=v1.Timeframe.M1,
            range_start=datetime(2026, 9, 3, 0, 0),
            range_end=_now(),
            trace_id=uuid4(),
        )


def test_candle_query_as_of_optional() -> None:
    query = v1.CandleQuery(key=_sample_key(), start=_now(), end=_now())
    assert query.as_of is None
    assert query.adjustment == v1.Adjustment.RAW


def test_replay_request_requires_as_of() -> None:
    with pytest.raises(ValidationError):
        v1.ReplayRequest(key=_sample_key(), start=_now(), end=_now())  # type: ignore[call-arg]


def test_replay_request_include_quarantined_locked_false() -> None:
    with pytest.raises(ValidationError):
        v1.ReplayRequest(
            key=_sample_key(),
            start=_now(),
            end=_now(),
            as_of=_now(),
            include_quarantined=True,  # type: ignore[arg-type]
        )


def test_instrument_ref_naive_listed_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.InstrumentRef(
            instrument_id=uuid4(),
            venue=v1.Venue.KIS_KRX,
            canonical_symbol="005930",
            venue_symbol="005930",
            asset_class=AssetClass.KR_EQUITY,
            base=None,
            quote=None,
            tick_size=Decimal("1"),
            lot_size=Decimal("1"),
            status=v1.SymbolStatus.LISTED,
            listed_at=datetime(2026, 9, 3, 0, 0),
            delisted_at=None,
        )


def test_corporate_action_split_ratio_roundtrip() -> None:
    action = v1.CorporateAction(
        action_type="SPLIT",
        instrument_id=uuid4(),
        ex_date=date(2026, 9, 3),
        ratio=Decimal("2"),
        source_ref="krx:notice:1",
    )
    assert action.ratio == Decimal("2")
    assert action.cash_amount is None


def test_calendar_day_naive_open_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.CalendarDay(
            venue=v1.Venue.KIS_KRX,
            trade_date=date(2026, 9, 3),
            is_trading_day=True,
            open_at=datetime(2026, 9, 3, 0, 0),
            close_at=_now(),
            source="test",
        )
