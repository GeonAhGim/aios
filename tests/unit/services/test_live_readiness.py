"""LP-1: 전환 근거 계산 -- 자료가 없으면 0이 아니라 None + 사유."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.services.live_readiness import (
    LiveReadinessInputError,
    compute_live_readiness,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _row(**kw):
    base = {
        "status": "FILLED",
        "side": "BUY",
        "quantity": "10",
        "filled_quantity": "10",
        "price": "100",
        "average_fill_price": "100",
        "created_at": T0,
        "updated_at": T0 + timedelta(hours=1),
    }
    base.update(kw)
    return base


def test_empty_input_reports_everything_unavailable_not_zero() -> None:
    r = compute_live_readiness([])
    assert r.order_count == 0 and r.terminal_count == 0
    assert r.fill_ratio is None and r.unfilled_ratio is None and r.reject_ratio is None
    assert r.avg_slippage_bps is None and r.fee_totals is None and r.net_pnl is None
    for key in ("fill_ratio", "avg_slippage_bps", "fee_totals", "net_pnl", "recovery_event_count"):
        assert key in r.unavailable and r.unavailable[key]


def test_net_pnl_and_recovery_are_always_unavailable_from_order_rows() -> None:
    r = compute_live_readiness([_row()])
    assert r.net_pnl is None and r.recovery_event_count is None
    assert "net_pnl" in r.unavailable and "recovery_event_count" in r.unavailable


def test_ratios_use_terminal_orders_only() -> None:
    rows = [
        _row(),  # FILLED
        _row(status="REJECTED", filled_quantity="0", average_fill_price=None),
        _row(status="CANCELLED", filled_quantity="0", average_fill_price=None),
        _row(status="ACKNOWLEDGED", filled_quantity="0", average_fill_price=None),  # 진행 중
    ]
    r = compute_live_readiness(rows)
    assert (r.order_count, r.terminal_count, r.open_count) == (4, 3, 1)
    assert r.fill_ratio == Decimal(1) / Decimal(3)
    assert r.reject_ratio == Decimal(1) / Decimal(3)
    assert r.unfilled_ratio == Decimal(1) / Decimal(3)


def test_unknown_status_is_not_treated_as_failure() -> None:
    r = compute_live_readiness(
        [_row(), _row(status="UNKNOWN", filled_quantity="0", average_fill_price=None)]
    )
    assert r.unknown_count == 1 and r.terminal_count == 1
    assert r.fill_ratio == Decimal(1)  # UNKNOWN은 분모에 들어가지 않는다
    assert r.reject_ratio == Decimal(0)


def test_partial_fills_are_counted_even_on_cancelled_orders() -> None:
    rows = [
        _row(status="CANCELLED", filled_quantity="4"),  # 4/10만 체결되고 취소
        _row(status="PARTIALLY_FILLED", filled_quantity="5"),  # 진행 중 부분체결
        _row(),
    ]
    r = compute_live_readiness(rows)
    assert r.partial_fill_count == 2
    assert r.partial_fill_ratio == Decimal(2) / Decimal(3)
    assert r.unfilled_count == 0  # 일부라도 체결됐으면 미체결로 세지 않는다


def test_slippage_is_signed_adverse_and_quantity_weighted() -> None:
    rows = [
        _row(
            side="BUY", price="100", average_fill_price="101", filled_quantity="10"
        ),  # +100bps 불리
        _row(
            side="SELL",
            quantity="30",
            price="100",
            average_fill_price="100.5",
            filled_quantity="30",
        ),  # -50bps 유리
    ]
    r = compute_live_readiness(rows)
    assert r.slippage_sample_size == 2
    # (100*10 + (-50)*30) / 40 = -12.5
    assert r.avg_slippage_bps == Decimal("-12.5")


def test_market_orders_without_reference_price_are_excluded_from_slippage() -> None:
    r = compute_live_readiness([_row(price=None, average_fill_price="101")])
    assert r.avg_slippage_bps is None and r.slippage_sample_size == 0
    assert "avg_slippage_bps" in r.unavailable


def test_fees_are_summed_per_currency_and_missing_column_is_unavailable() -> None:
    with_fee = compute_live_readiness(
        [
            _row(fee_total="1.5", fee_currency="USDT"),
            _row(fee_total="0.5", fee_currency="USDT"),
            _row(fee_total="2", fee_currency="KRW"),
        ]
    )
    assert with_fee.fee_totals == {"USDT": Decimal("2.0"), "KRW": Decimal("2")}
    assert "fee_totals" not in with_fee.unavailable
    no_fee = compute_live_readiness([_row()])
    assert no_fee.fee_totals is None and "fee_totals" in no_fee.unavailable


def test_period_spans_first_creation_to_last_update() -> None:
    rows = [_row(), _row(created_at=T0 + timedelta(days=1), updated_at=T0 + timedelta(days=2))]
    r = compute_live_readiness(rows)
    assert r.period_start == T0 and r.period_end == T0 + timedelta(days=2)
    assert r.period_days == Decimal(2)


@pytest.mark.parametrize(
    "bad",
    [
        {"quantity": "0"},
        {"quantity": "-1"},
        {"filled_quantity": "11"},
        {"filled_quantity": "-1"},
        {"side": "HOLD"},
        {"quantity": "abc"},
    ],
)
def test_invalid_rows_are_rejected_loudly(bad) -> None:
    with pytest.raises(LiveReadinessInputError):
        compute_live_readiness([_row(**bad)])


def test_partial_fee_coverage_is_unavailable_not_an_underreported_total() -> None:
    """일부 주문에만 수수료가 있으면 합계를 내지 않는다 -- 과소 보고가 좋은 숫자로 안 보이게."""
    r = compute_live_readiness([_row(fee_total="1", fee_currency="USDT"), _row()])
    assert r.fee_totals is None
    assert "일부" in r.unavailable["fee_totals"]


def test_input_stream_failure_does_not_return_partial_evidence() -> None:
    def rows():
        yield _row(fee_total="1", fee_currency="USDT")
        raise RuntimeError("injected source failure")

    with pytest.raises(RuntimeError, match="injected source failure"):
        compute_live_readiness(rows())


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "FAILED"])
def test_terminal_without_fill_is_unfilled(status: str) -> None:
    r = compute_live_readiness(iter([_row(status=status, filled_quantity="0")]))
    assert r.unfilled_count == r.terminal_count == 1
    assert r.filled_count == r.partial_fill_count == r.open_count == 0
    assert r.unfilled_ratio == Decimal(1)
    assert r.slippage_sample_size == 0


def test_readiness_functions_remain_within_complexity_cap() -> None:
    from pathlib import Path

    import src.services.live_readiness as module
    from scripts.check_complexity import scan_file

    path = Path(module.__file__)
    assert scan_file(path.name, path.read_text(encoding="utf-8")) == []
