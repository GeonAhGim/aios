"""LB-4 — pnl 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-4
(`unit/positions/test_pnl.py`: "multiplier·FX 결합, quantize").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.exceptions import CurrencyMismatchError
from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain import fx, pnl

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    quantity: Decimal = Decimal("10"),
    avg_cost: Decimal = Decimal("100"),
    avg_cost_currency: Currency = Currency.USDT,
    base_currency: Currency = Currency.USDT,
    realized_pnl_base: Decimal = Decimal("0"),
    fees_base: Decimal = Decimal("0"),
    funding_base: Decimal = Decimal("0"),
) -> PositionSnapshotView:
    return PositionSnapshotView(
        position_key="BITGET:BTCUSDT:strat:exec",
        tenant_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=avg_cost, currency=avg_cost_currency),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=realized_pnl_base,
        unrealized_pnl_base=None,
        fees_base=fees_base,
        funding_base=funding_base,
        mark_price=None,
        mark_at=None,
        base_currency=base_currency,
        last_journal_seq=1,
        updated_at=_NOW,
    )


def test_unrealized_same_currency_no_fx_needed() -> None:
    snapshot = _snapshot(quantity=Decimal("10"), avg_cost=Decimal("100"))
    mark = Money(amount=Decimal("110"), currency=Currency.USDT)

    result = pnl.unrealized(snapshot, mark, None)

    assert result.unrealized == Decimal("100")  # (110-100) * 10
    assert result.fx_rates_used == []


def test_unrealized_combines_multiplier() -> None:
    snapshot = _snapshot(quantity=Decimal("2"), avg_cost=Decimal("100"))
    mark = Money(amount=Decimal("110"), currency=Currency.USDT)

    result = pnl.unrealized(snapshot, mark, None, contract_multiplier=Decimal("5"))

    assert result.unrealized == Decimal("100")  # (110-100) * 2 * 5


def test_unrealized_zero_quantity_skips_fx_entirely() -> None:
    """열린 수량이 없으면 환율이 없어도(None) 실패하지 않는다."""
    snapshot = _snapshot(
        quantity=Decimal("0"), avg_cost_currency=Currency.KRW, base_currency=Currency.USDT
    )
    mark = Money(amount=Decimal("999"), currency=Currency.KRW)

    result = pnl.unrealized(snapshot, mark, None)

    assert result.unrealized == Decimal("0")
    assert result.fx_rates_used == []


def test_unrealized_converts_to_base_currency_via_fx() -> None:
    snapshot = _snapshot(
        quantity=Decimal("10"),
        avg_cost=Decimal("100"),
        avg_cost_currency=Currency.USDT,
        base_currency=Currency.KRW,
    )
    mark = Money(amount=Decimal("110"), currency=Currency.USDT)
    rate = FXRate(
        base=Currency.USDT, quote=Currency.KRW, rate=Decimal("1350"), timestamp=_NOW, source="test"
    )

    result = pnl.unrealized(snapshot, mark, rate, now=_NOW)

    assert result.unrealized == Decimal("100") * Decimal("1350")
    assert result.base_currency == Currency.KRW
    assert result.fx_rates_used == [rate]


def test_unrealized_missing_fx_rate_raises_no_silent_fallback() -> None:
    snapshot = _snapshot(avg_cost_currency=Currency.USDT, base_currency=Currency.KRW)
    mark = Money(amount=Decimal("110"), currency=Currency.USDT)

    with pytest.raises(fx.FxRateMissingError):
        pnl.unrealized(snapshot, mark, None)


def test_unrealized_stale_fx_rate_raises() -> None:
    snapshot = _snapshot(avg_cost_currency=Currency.USDT, base_currency=Currency.KRW)
    mark = Money(amount=Decimal("110"), currency=Currency.USDT)
    stale_rate = FXRate(
        base=Currency.USDT,
        quote=Currency.KRW,
        rate=Decimal("1350"),
        timestamp=_NOW - timedelta(hours=1),
        source="test",
    )

    with pytest.raises(fx.FxRateStaleError):
        pnl.unrealized(snapshot, mark, stale_rate, now=_NOW, max_age=timedelta(minutes=5))


def test_unrealized_currency_mismatch_between_mark_and_avg_cost_raises() -> None:
    snapshot = _snapshot(avg_cost_currency=Currency.USDT, base_currency=Currency.USDT)
    mark = Money(amount=Decimal("110"), currency=Currency.KRW)

    with pytest.raises(CurrencyMismatchError):
        pnl.unrealized(snapshot, mark, None)


def test_pnl_total_is_exact_sum_of_components_no_rounding_residual() -> None:
    """합 보존: total은 realized+unrealized+fees+funding의 정확한 대수합이다
    (LC-2 rounding과 같은 정신 — 잔차 없음, 근사 비교 금지)."""
    snapshot = _snapshot(
        quantity=Decimal("3"),
        avg_cost=Decimal("33.33"),
        realized_pnl_base=Decimal("17.77"),
        fees_base=Decimal("-1.11"),
        funding_base=Decimal("0.09"),
    )
    mark = Money(amount=Decimal("40.01"), currency=Currency.USDT)

    result = pnl.unrealized(snapshot, mark, None)

    assert result.total == result.realized + result.unrealized + result.fees + result.funding


def test_pnl_breakdown_carries_realized_fees_funding_from_snapshot_unchanged() -> None:
    snapshot = _snapshot(
        realized_pnl_base=Decimal("12.5"),
        fees_base=Decimal("-0.75"),
        funding_base=Decimal("0.25"),
    )
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)

    result = pnl.unrealized(snapshot, mark, None)

    assert result.realized == Decimal("12.5")
    assert result.fees == Decimal("-0.75")
    assert result.funding == Decimal("0.25")
