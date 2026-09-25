"""TEST-cov(task-4631): src/api/schemas/execution.py coverage.

Covers request/response schema validation (incl. negative/boundary cases) and
the to_*_response converters against their upstream service models.
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.api.schemas.execution import (
    ConvertToLiveRequest,
    ExecutionCardResponse,
    ExecutionCreateRequest,
    ExecutionResponse,
    RetireRequest,
    SetMaxDrawdownRequest,
    to_execution_card_response,
    to_execution_response,
)
from src.services.execution_monitoring_service import ExecutionCard
from src.services.execution_types import ExecutionSummary


def _call_untyped(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke `fn` outside mypy's static arg checking.

    These negative tests deliberately pass missing/wrong-typed arguments to
    assert pydantic's runtime validation rejects them.
    """
    return fn(*args, **kwargs)


def test_execution_create_request_accepts_valid_payload() -> None:
    req = ExecutionCreateRequest(
        strategy_id="s1",
        strategy_version="v1",
        allocated_capital=Decimal("1000"),
        currency="USD",
        exchange="bitget",
        mode="paper",
    )
    assert req.allocated_capital == Decimal("1000")


def test_execution_create_request_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(
            ExecutionCreateRequest,
            strategy_id="s1",
            strategy_version="v1",
            allocated_capital=Decimal("1000"),
            currency="USD",
            exchange="bitget",
        )


def test_execution_create_request_rejects_non_decimal_capital() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(
            ExecutionCreateRequest,
            strategy_id="s1",
            strategy_version="v1",
            allocated_capital="not-a-number",
            currency="USD",
            exchange="bitget",
            mode="paper",
        )


def test_convert_to_live_request_accepts_zero_capital_boundary() -> None:
    req = ConvertToLiveRequest(allocated_capital=Decimal("0"), currency="USD", exchange="bitget")
    assert req.allocated_capital == Decimal("0")


def test_convert_to_live_request_rejects_missing_exchange() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(ConvertToLiveRequest, allocated_capital=Decimal("100"), currency="USD")


def test_retire_request_defaults_to_keep_positions() -> None:
    req = RetireRequest()
    assert req.liquidation == "KEEP_POSITIONS"


def test_retire_request_accepts_explicit_liquidation() -> None:
    req = RetireRequest(liquidation="LIQUIDATE")
    assert req.liquidation == "LIQUIDATE"


def test_set_max_drawdown_request_defaults_to_none() -> None:
    req = SetMaxDrawdownRequest()
    assert req.max_drawdown_pct is None


def test_set_max_drawdown_request_rejects_non_decimal_value() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(SetMaxDrawdownRequest, max_drawdown_pct="not-a-number")


def test_execution_response_allows_optional_fields_to_be_none() -> None:
    resp = ExecutionResponse(
        id=1,
        status="RUNNING",
        mode="paper",
        exchange="bitget",
        allocated_capital=Decimal("500"),
    )
    assert resp.approval_request_id is None
    assert resp.max_drawdown_pct is None


def test_to_execution_response_maps_all_fields_from_summary() -> None:
    summary = ExecutionSummary(
        id=7,
        status="RUNNING",
        mode="live",
        exchange="bitget",
        allocated_capital=Decimal("1234.56"),
        approval_request_id=99,
        max_drawdown_pct=Decimal("10.5"),
    )

    resp = to_execution_response(summary)

    assert isinstance(resp, ExecutionResponse)
    assert resp.id == 7
    assert resp.status == "RUNNING"
    assert resp.mode == "live"
    assert resp.exchange == "bitget"
    assert resp.allocated_capital == Decimal("1234.56")
    assert resp.approval_request_id == 99
    assert resp.max_drawdown_pct == Decimal("10.5")


def test_execution_card_response_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(
            ExecutionCardResponse,
            execution_id=1,
            strategy_id="s1",
            strategy_version="v1",
            status="RUNNING",
            mode="paper",
            exchange="bitget",
            allocated_capital=Decimal("100"),
            days_since_start=None,
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            # max_drawdown_pct intentionally omitted (no default on this field)
        )


def test_to_execution_card_response_maps_all_fields_from_card() -> None:
    card = ExecutionCard(
        execution_id=3,
        strategy_id="s1",
        strategy_version="v1",
        status="RUNNING",
        mode="paper",
        exchange="bitget",
        allocated_capital=Decimal("100"),
        days_since_start=5,
        realized_pnl=Decimal("1.5"),
        unrealized_pnl=Decimal("-0.5"),
        max_drawdown_pct=Decimal("2.5"),
    )

    resp = to_execution_card_response(card)

    assert isinstance(resp, ExecutionCardResponse)
    assert resp.execution_id == 3
    assert resp.days_since_start == 5
    assert resp.realized_pnl == Decimal("1.5")
    assert resp.unrealized_pnl == Decimal("-0.5")
    assert resp.max_drawdown_pct == Decimal("2.5")
