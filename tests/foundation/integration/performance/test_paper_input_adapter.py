"""PaperStatementInputAdapter 통합테스트 — 실제 dev/test DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L48 DoD:
"미리컨실 409, 입력 조립")."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.performance.adapters.paper_input_adapter import (
    PaperStatementInputAdapter,
    UnreconciledInputError,
)
from src.foundation.performance.domain.models import ValuationState
from tests.foundation.integration.performance.conftest import (
    create_paper_execution,
    insert_filled_order,
    insert_position,
    set_reconciliation_state,
)
from tests.integration.conftest import create_test_user

_NOW = datetime.now(timezone.utc)
_PERIOD_START = _NOW - timedelta(days=1)


@pytest.fixture
def adapter(pool):
    return PaperStatementInputAdapter(pool)


async def test_load_reconciled_snapshots_raises_when_never_reconciled(pool, adapter):
    user_id = await create_test_user(pool)

    with pytest.raises(UnreconciledInputError) as exc_info:
        await adapter.load_reconciled_snapshots(
            scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
        )
    assert exc_info.value.reason_code == "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


async def test_load_reconciled_snapshots_raises_when_material_mismatch(pool, adapter):
    user_id = await create_test_user(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="MATERIAL_MISMATCH")

    with pytest.raises(UnreconciledInputError):
        await adapter.load_reconciled_snapshots(
            scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
        )


async def test_load_reconciled_snapshots_returns_snapshot_when_healthy(pool, adapter):
    user_id = await create_test_user(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    execution_id = await create_paper_execution(
        pool, user_id, allocated_capital=Decimal("1000")
    )
    await insert_position(
        pool,
        user_id,
        execution_id,
        entry_time=_NOW - timedelta(hours=1),
        quantity=Decimal("2"),
        average_entry_price=Decimal("100"),
        unrealized_pnl=Decimal("5"),
        realized_pnl=Decimal("1"),
    )

    period_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    snapshots = await adapter.load_reconciled_snapshots(
        scope_ref=str(user_id), period_start=_PERIOD_START, period_end=period_end
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.state == ValuationState.RECONCILED
    assert snapshot.tenant_id == user_id
    assert len(snapshot.positions) == 1
    assert Decimal(snapshot.positions[0]["realized_pnl"]) == Decimal("1")
    # cash = allocated_capital(1000) - deployed_notional(2 * 100)
    assert snapshot.cash == Decimal("800")


async def test_load_reconciled_snapshots_resolved_status_is_trusted(pool, adapter):
    user_id = await create_test_user(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="RESOLVED")

    snapshots = await adapter.load_reconciled_snapshots(
        scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
    )
    assert len(snapshots) == 1


async def test_load_fills_returns_only_filled_paper_orders(pool, adapter):
    user_id = await create_test_user(pool)
    execution_id = await create_paper_execution(pool, user_id)
    await insert_filled_order(
        pool, user_id, execution_id, average_fill_price=Decimal("123.45"),
        filled_quantity=Decimal("0.5"),
    )

    fills = await adapter.load_fills(
        scope_ref=str(user_id),
        period_start=_PERIOD_START,
        period_end=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert len(fills) == 1
    assert Decimal(fills[0]["average_fill_price"]) == Decimal("123.45")
    assert fills[0]["fee"] is None  # orders에 fee 컬럼이 없다 — 항상 PENDING


async def test_load_cashflows_returns_allocated_capital_as_deposit(pool, adapter):
    user_id = await create_test_user(pool)
    started_at = _NOW - timedelta(hours=2)
    await create_paper_execution(
        pool, user_id, allocated_capital=Decimal("500"), started_at=started_at
    )

    cashflows = await adapter.load_cashflows(
        scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
    )

    assert len(cashflows) == 1
    assert cashflows[0].amount == Decimal("500")
    assert cashflows[0].kind.value == "DEPOSIT"
