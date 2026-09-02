"""LB-1 — positions/contracts/v1 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2 (B), §9 LB-1.

`fixtures/positions_contracts_v1.json`은 현재 스키마의 스냅샷이다. 필드를
지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드 제거 시
실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만 fixture를
함께 갱신한다.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency, FXRate, Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts import v1

FIXTURE = Path(__file__).parent / "fixtures" / "positions_contracts_v1.json"

_MODELS = (
    v1.RecordFillCommand,
    v1.RecordFundingCommand,
    v1.PositionJournalEntryView,
    v1.Lot,
    v1.PositionSnapshotView,
    v1.PnLBreakdown,
    v1.NAVSnapshot,
    v1.RebuildReport,
)

_ERROR_CODES = {
    "POS_IDEMPOTENT_REPLAY",
    "POS_IDEMPOTENCY_DIGEST_MISMATCH",
    "POS_SEQUENCE_CONFLICT",
    "POS_NEGATIVE_QUANTITY",
    "POS_FX_RATE_MISSING",
    "POS_MARK_STALE",
    "POS_NAV_CHAIN_BROKEN",
    "POS_ACCOUNT_UNKNOWN",
}


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _sample_fill_command(**overrides: object) -> v1.RecordFillCommand:
    base: dict[str, object] = dict(
        tenant_id=uuid4(),
        account_id=uuid4(),
        position_key="acct-1:BTC/USDT",
        order_id=uuid4(),
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("0.5"),
        price=Money(amount=Decimal("50000"), currency=Currency.USDT),
        fee=Money(amount=Decimal("5"), currency=Currency.USDT),
        occurred_at=_now(),
        trace_id=uuid4(),
    )
    base.update(overrides)
    return v1.RecordFillCommand(**base)  # type: ignore[arg-type]


def _sample_lot(**overrides: object) -> v1.Lot:
    base: dict[str, object] = dict(
        quantity=Decimal("0.5"),
        unit_cost=Decimal("50000"),
        opened_at=_now(),
    )
    base.update(overrides)
    return v1.Lot(**base)  # type: ignore[arg-type]


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_position_error_code_has_exactly_the_taxonomy_from_spec() -> None:
    assert {code.value for code in v1.PositionErrorCode} == _ERROR_CODES


def test_record_fill_command_naive_occurred_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_fill_command(occurred_at=datetime(2026, 9, 3, 0, 0))


def test_record_fill_command_accepts_aware_occurred_at() -> None:
    cmd = _sample_fill_command()
    assert cmd.occurred_at.tzinfo is not None
    assert cmd.schema_version == "v1"


def test_record_funding_command_naive_occurred_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.RecordFundingCommand(
            tenant_id=uuid4(),
            account_id=uuid4(),
            position_key="acct-1:BTC/USDT",
            funding_id="f-1",
            amount=Money(amount=Decimal("-1.5"), currency=Currency.USDT),
            rate=Decimal("0.0001"),
            occurred_at=datetime(2026, 9, 3, 0, 0),
            trace_id=uuid4(),
        )


def test_lot_naive_opened_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_lot(opened_at=datetime(2026, 9, 3, 0, 0))


def test_position_journal_entry_view_naive_recorded_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.PositionJournalEntryView(
            id=1,
            position_key="acct-1:BTC/USDT",
            sequence_no=1,
            entry_type=v1.JournalEntryType.FILL,
            qty_delta=Decimal("0.5"),
            price=Money(amount=Decimal("50000"), currency=Currency.USDT),
            fee=None,
            realized_pnl_base=Decimal("0"),
            fx_rate=None,
            fx_source=None,
            source_event_type="FILL",
            source_event_id="order:1:1",
            idempotency_key="fill:order-1:1",
            prev_hash=None,
            entry_hash="e" * 64,
            occurred_at=_now(),
            recorded_at=datetime(2026, 9, 3, 0, 0),
        )


def test_position_snapshot_view_unrealized_pnl_defaults_to_none_without_mark() -> None:
    snapshot = v1.PositionSnapshotView(
        position_key="acct-1:BTC/USDT",
        tenant_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        quantity=Decimal("0.5"),
        avg_cost=Money(amount=Decimal("50000"), currency=Currency.USDT),
        cost_method=v1.CostMethod.FIFO,
        lots=[_sample_lot()],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("5"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.USDT,
        last_journal_seq=1,
        updated_at=_now(),
    )
    assert snapshot.unrealized_pnl_base is None
    assert snapshot.mark_at is None


def test_position_snapshot_view_naive_mark_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.PositionSnapshotView(
            position_key="acct-1:BTC/USDT",
            tenant_id=uuid4(),
            account_id=uuid4(),
            instrument_id=uuid4(),
            quantity=Decimal("0.5"),
            avg_cost=Money(amount=Decimal("50000"), currency=Currency.USDT),
            cost_method=v1.CostMethod.FIFO,
            lots=[],
            realized_pnl_base=Decimal("0"),
            unrealized_pnl_base=Decimal("10"),
            fees_base=Decimal("5"),
            funding_base=Decimal("0"),
            mark_price=Money(amount=Decimal("51000"), currency=Currency.USDT),
            mark_at=datetime(2026, 9, 3, 0, 0),
            base_currency=Currency.USDT,
            last_journal_seq=1,
            updated_at=_now(),
        )


def test_pnl_breakdown_total_roundtrip() -> None:
    breakdown = v1.PnLBreakdown(
        realized=Decimal("10"),
        unrealized=Decimal("5"),
        fees=Decimal("-1"),
        funding=Decimal("-0.5"),
        total=Decimal("13.5"),
        base_currency=Currency.USDT,
        fx_rates_used=[
            FXRate(
                base=Currency.USDT,
                quote=Currency.KRW,
                rate=Decimal("1350"),
                timestamp=_now(),
                source="test",
            )
        ],
    )
    assert breakdown.total == Decimal("13.5")
    assert breakdown.fx_rates_used[0].source == "test"


def test_nav_snapshot_chain_equation_roundtrip() -> None:
    nav = v1.NAVSnapshot(
        account_id=uuid4(),
        nav_date=date(2026, 9, 3),
        base_currency=Currency.USDT,
        opening_nav=Decimal("1000"),
        cash=Decimal("400"),
        positions_mv=Decimal("620"),
        realized=Decimal("10"),
        unrealized_delta=Decimal("5"),
        funding=Decimal("0"),
        fees=Decimal("-1"),
        flows=Decimal("6"),
        closing_nav=Decimal("1020"),
        fx_rates=[],
        source_hash="h" * 64,
    )
    assert nav.closing_nav == nav.cash + nav.positions_mv


def test_rebuild_report_drift_tuple_roundtrip() -> None:
    report = v1.RebuildReport(
        position_key="acct-1:BTC/USDT",
        entries=42,
        drift={"quantity": (Decimal("0.5"), Decimal("0.5"))},
        applied=False,
    )
    assert report.drift["quantity"] == (Decimal("0.5"), Decimal("0.5"))
    assert report.applied is False
