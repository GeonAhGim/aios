"""LA-1 — market_data/contracts/v1 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

`fixtures/market_data_contracts_v1.json`은 현재 스키마의 스냅샷이다. 필드를
지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드 제거 시
실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만 fixture를 함께
갱신한다.
"""

import json
import time
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


# ── DEEPEN: negative tests (LA-1) ──────────────────────────────────────────


def test_register_instrument_command_naive_listed_at_rejected() -> None:
    """RegisterInstrumentCommand.listed_at은 AwareDatetime — naive 거부."""
    with pytest.raises(ValidationError):
        v1.RegisterInstrumentCommand(
            venue=v1.Venue.KIS_KRX,
            venue_symbol="005930.KS",
            asset_class=AssetClass.KR_EQUITY,
            tick_size=Decimal("1"),
            lot_size=Decimal("1"),
            listed_at=datetime(2026, 9, 3, 0, 0),
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
        )


def test_lifecycle_event_command_naive_effective_at_rejected() -> None:
    """LifecycleEventCommand.effective_at은 AwareDatetime — naive 거부."""
    with pytest.raises(ValidationError):
        v1.LifecycleEventCommand(
            instrument_id=uuid4(),
            event="LIST",
            effective_at=datetime(2026, 9, 3, 0, 0),
            source_ref="test",
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
        )


def test_quality_issue_naive_open_time_rejected() -> None:
    """QualityIssue.open_time가 AwareDatetime | None — None은 허용, naive datetime은 거부."""
    with pytest.raises(ValidationError):
        v1.QualityIssue(
            type=v1.QualityIssueType.GAP,
            severity=v1.Severity.REJECT,
            open_time=datetime(2026, 9, 3, 0, 0),
            detail={"detail": "gap detected"},
        )


def test_ingest_batch_result_missing_required_rejected() -> None:
    """IngestBatchResult는 모든 필드가 NOT NULL — 누락 시 ValidationError."""
    with pytest.raises(ValidationError):
        v1.IngestBatchResult.model_validate(
            {
                "batch_id": str(uuid4()),
                "source": "test",
                "venue": "KIS_KRX",
                "instrument_id": str(uuid4()),
                "timeframe": "M1",
                "range_start": _now().isoformat(),
                "range_end": _now().isoformat(),
                "request_fingerprint": "abc",
                "verdict": {
                    "verdict": "ACCEPT",
                    "accepted": 1,
                    "quarantined": 0,
                    "rejected": 0,
                    "issues": [],
                },
                "batch_hash": "sha256fake",
                # audit_event_id 누락
            }
        )


def test_data_quality_metrics_missing_key_rejected() -> None:
    """DataQualityMetrics.key는 필수 — 누락 시 ValidationError."""
    with pytest.raises(ValidationError):
        v1.DataQualityMetrics.model_validate(
            {
                "staleness_s": 300,
                "gap_ratio_24h": "0.01",
                "reject_ratio_24h": "0.00",
                "last_batch_id": None,
            }
        )


# ── DEEPEN: numeric 패턴(^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$) invalid numeric (LA-1) ──


def test_candle_record_multiple_decimal_points_rejected() -> None:
    """Decimal 필드 문자열에 소수점이 두 개면 스키마 pattern 위반으로 거부된다."""
    with pytest.raises(ValidationError):
        _sample_candle(open="1.2.3")


def test_candle_record_thousands_separator_rejected() -> None:
    """Decimal 필드 문자열에 천단위 콤마가 있으면 스키마 pattern 위반으로 거부된다."""
    with pytest.raises(ValidationError):
        _sample_candle(high="1,000.50")


def test_candle_record_lone_sign_numeric_rejected() -> None:
    """부호 문자만 있는 문자열("-")은 numeric pattern의 음의 전방탐색에 걸려 거부된다."""
    with pytest.raises(ValidationError):
        _sample_candle(low="-")


# ── DEEPEN: failure-injection test (LA-1) ─────────────────────────────────


def test_quality_issue_detail_type_enforced() -> None:
    """QualityIssue.detail은 dict[str, str] — value가 str이 아니면 거부.

    model_validate로 dict에 int value를 주입해 런타임 검증 거부를 유도한다.
    """
    with pytest.raises(ValidationError):
        v1.QualityIssue.model_validate(
            {
                "type": "SPIKE",
                "severity": "WARN",
                "open_time": None,
                "detail": {"price": 12345},
            }
        )


def test_fixture_read_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIXTURE.read_text가 실패하면 스냅샷 테스트가 예외를 삼키지 않고 그대로 전파해야 한다.

    monkeypatch로 Path.read_text에 의존성 예외(OSError)를 주입한다(107번 §8).
    """
    original_read_text = Path.read_text

    def _boom(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if self == FIXTURE:
            raise OSError("simulated fixture read failure")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(OSError):
        json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── DEEPEN: performance assertion (LA-1) ───────────────────────────────────


@pytest.mark.perf  # wall-clock budget: serial perf stage (task-7434 guard)
def test_candle_record_bulk_validation_throughput() -> None:
    """1,000건 CandleRecord 검증이 예산(200ms) 내에 끝나야 한다 — O(n) 이상 회귀 감지."""
    start = time.perf_counter()
    for _ in range(1_000):
        _sample_candle()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 200, f"1,000건 CandleRecord 검증이 {elapsed_ms:.1f}ms — 200ms 예산 초과"
