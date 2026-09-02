"""LB-6 — nav 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-6
("체인 등식", `unit/positions/test_nav.py`: "체인 성립/불성립, closing=cash+mv").
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.positions.contracts.v1 import NAVSnapshot
from src.foundation.positions.domain import nav

_ACCOUNT = uuid4()
_DAY = date(2026, 9, 3)


def _inputs(**overrides: object) -> nav.NavInputs:
    fields: dict[str, object] = {
        "account_id": _ACCOUNT,
        "nav_date": _DAY,
        "base_currency": Currency.USDT,
        "opening_nav": Decimal("1000"),
        "cash": Decimal("400"),
        "position_mvs": [Decimal("300"), Decimal("250")],
        "realized": Decimal("30"),
        "unrealized_delta": Decimal("15"),
        "funding": Decimal("2"),
        "fees": Decimal("3"),
        "flows": Decimal("0"),
    }
    fields.update(overrides)
    return nav.NavInputs(**fields)  # type: ignore[arg-type]


def _snapshot(**overrides: object) -> NAVSnapshot:
    computed = nav.compute_daily_nav(_inputs())
    return computed.model_copy(update=overrides)


def test_compute_daily_nav_closing_is_cash_plus_sum_of_position_mvs() -> None:
    result = nav.compute_daily_nav(_inputs())

    assert result.positions_mv == Decimal("550")  # 300 + 250
    assert result.closing_nav == Decimal("950")  # 400 + 550


def test_compute_daily_nav_with_no_open_positions_closing_is_cash() -> None:
    result = nav.compute_daily_nav(_inputs(position_mvs=[]))

    assert result.positions_mv == Decimal("0")
    assert result.closing_nav == result.cash


def test_compute_daily_nav_source_hash_is_deterministic() -> None:
    first = nav.compute_daily_nav(_inputs())
    second = nav.compute_daily_nav(_inputs())

    assert first.source_hash == second.source_hash


def test_compute_daily_nav_source_hash_changes_with_inputs() -> None:
    first = nav.compute_daily_nav(_inputs())
    second = nav.compute_daily_nav(_inputs(cash=Decimal("401")))

    assert first.source_hash != second.source_hash


def test_verify_chain_holds_when_both_equations_balance() -> None:
    # opening(1000) + realized(30) + Δunrealized(15) + funding(2) - fees(3) + flows(0) = 1044
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("30"),
        unrealized_delta=Decimal("15"),
        funding=Decimal("2"),
        fees=Decimal("3"),
        flows=Decimal("0"),
        closing_nav=Decimal("1044"),
    )

    nav.verify_chain(prev, cur)  # 예외 없음


def test_verify_chain_rejects_opening_not_equal_to_prev_closing() -> None:
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(opening_nav=Decimal("999"), closing_nav=Decimal("1043"))

    with pytest.raises(nav.NavChainBrokenError):
        nav.verify_chain(prev, cur)


def test_verify_chain_rejects_closing_not_matching_rollforward_equation() -> None:
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("30"),
        unrealized_delta=Decimal("15"),
        funding=Decimal("2"),
        fees=Decimal("3"),
        flows=Decimal("0"),
        closing_nav=Decimal("1044.01"),  # off by 0.01
    )

    with pytest.raises(nav.NavChainBrokenError):
        nav.verify_chain(prev, cur)


def test_verify_chain_does_not_absorb_rounding_even_by_smallest_decimal_unit() -> None:
    """허용오차 0 — 소수점 최소단위 차이도 반올림으로 흡수하지 않는다."""
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("0.0000000001"),
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("0"),
        closing_nav=Decimal("1000"),  # 정확히는 1000.0000000001이어야 함
    )

    with pytest.raises(nav.NavChainBrokenError):
        nav.verify_chain(prev, cur)


def test_verify_chain_accepts_negative_flows_and_fees_correctly() -> None:
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("-20"),
        unrealized_delta=Decimal("-5"),
        funding=Decimal("0"),
        fees=Decimal("1"),
        flows=Decimal("-50"),
        closing_nav=Decimal("924"),  # 1000 - 20 - 5 + 0 - 1 - 50
    )

    nav.verify_chain(prev, cur)  # 예외 없음
