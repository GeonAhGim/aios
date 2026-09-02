"""Performance 계약(v1) 테스트 — 107번 §3 호환 규칙.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L45 DoD)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.performance.contracts.v1 import (
    SCHEMA_VERSION,
    ComponentBreakdown,
    ComputeStatementCommand,
    MoneyValue,
    PerformanceStatementView,
    ReturnValue,
    StatementScope,
    StatementState,
)

_NOW = datetime.now(timezone.utc)


def _money(amount: Decimal | None = Decimal("100")) -> MoneyValue:
    return MoneyValue(amount=amount, currency="USDT", precision=2, as_of=_NOW, state="FINAL")


def _breakdown() -> ComponentBreakdown:
    return ComponentBreakdown(
        gross_pnl=_money(),
        fees=_money(Decimal("1")),
        slippage=_money(Decimal("0")),
        funding=_money(Decimal("0")),
        fx=_money(Decimal("0")),
        cashflows_net=_money(Decimal("0")),
        estimated_tax=_money(Decimal("0")),
        net_pnl=_money(Decimal("99")),
    )


def _statement(**overrides) -> PerformanceStatementView:
    defaults = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        scope=StatementScope.PAPER,
        scope_ref="deployment-1",
        period_start=_NOW,
        period_end=_NOW,
        as_of=_NOW,
        methodology_version="pm-v1",
        methodology_hash="abc123",
        input_refs=["snapshot:abc"],
        components=_breakdown(),
        returns=[],
        risk={"vol_pct": None},
        benchmark=None,
        benchmark_ref=None,
        state=StatementState.ESTIMATED,
        revision_no=1,
        prior_statement_id=None,
        identity_ok=True,
        identity_residual=Decimal("0"),
        limitations=[],
        evidence_refs=["audit:1"],
    )
    defaults.update(overrides)
    return PerformanceStatementView(**defaults)


def test_statement_view_round_trips_through_json():
    statement = _statement()
    restored = PerformanceStatementView.model_validate_json(statement.model_dump_json())
    assert restored.components.net_pnl.amount == Decimal("99")
    assert restored.schema_version == SCHEMA_VERSION


def test_money_value_none_amount_means_pending_not_zero():
    """PENDING(미리컨실)은 None으로 표현한다 — 0과 구분돼야 한다."""
    pending = _money(amount=None)
    assert pending.amount is None
    restored = MoneyValue.model_validate_json(pending.model_dump_json())
    assert restored.amount is None


def test_statement_required_fields_cannot_be_omitted():
    """계약이 실수로 optional화되지 않았는지 — 필수 필드 하나만 빼도 거부."""
    payload = _statement().model_dump()
    del payload["identity_ok"]
    with pytest.raises(ValidationError):
        PerformanceStatementView(**payload)


def test_return_value_basis_and_method_are_constrained_literals():
    with pytest.raises(ValidationError):
        ReturnValue(
            value_pct=Decimal("1"),
            basis="NOT_A_BASIS",  # type: ignore[arg-type]
            method="TWR",
            period_start=_NOW,
            period_end=_NOW,
            annualized=False,
            periods_per_year=None,
        )


def test_compute_statement_command_defaults_methodology_to_none():
    command = ComputeStatementCommand(
        scope=StatementScope.PAPER,
        scope_ref="deployment-1",
        period_start=_NOW,
        period_end=_NOW,
    )
    assert command.methodology_version is None
