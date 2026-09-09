"""EM-A2 adversarial suite -- proves a child order cannot be created without
going through `submit_order()`'s CM-8 gate, and that `reserve_child_slice`
(EM-A1/EM-A4) actually rejects an over-slice / terminal-parent attempt.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §8 ("자식이 게이트
우회 시도, 부모 초과 슬라이스... 터미널 부모에 자식 생성"), §9 EM-3 DoD
("EM-A2 적대적 통과"). task-2121.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from scripts.check_child_order_path import (
    block_sets_child_columns,
    find_bypasses,
    find_insert_orders_blocks,
)
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.ems.application.aggregate_parent import reserve_child_slice
from src.foundation.ems.domain.parent_child import AlgoConstraintError, ParentTerminalError
from src.services.oms.contracts.v1_views import OrderView

_ATTACK_SNIPPET = """
async def sneaky_child_insert(conn, parent_id):
    await conn.execute(
        "INSERT INTO orders (order_id, user_id, symbol, quantity, "
        "parent_order_id, algo_run_id) VALUES ($1, $2, $3, $4, $5, $6)",
        order_id, user_id, symbol, quantity, parent_id, algo_run_id,
    )
"""


def test_checker_catches_a_bypass_insert_outside_submit_order() -> None:
    """If some future module inlined its own `INSERT INTO orders(...
    parent_order_id...)`, the EM-3 static check must flag it -- proven here
    against a synthetic snippet so the real repo scan (below) staying clean
    isn't just because the regex never matches anything."""
    violations = find_bypasses("src/services/oms/adapters/sneaky.py", _ATTACK_SNIPPET)
    assert violations, "checker failed to flag a synthetic child-order INSERT bypass"


def test_checker_allows_the_same_insert_inside_submit_order_py() -> None:
    """The one allowlisted file is exempt by construction (it IS the gate)."""
    violations = find_bypasses(
        "src/services/oms/application/submit_order.py", _ATTACK_SNIPPET
    )
    assert violations == []


def test_repo_has_no_bypass_today() -> None:
    """Regression pin -- the real repository, scanned the same way
    `scripts/check_child_order_path.py` scans it in CI, is clean right now."""
    from scripts.check_child_order_path import _REPO_ROOT, _iter_production_files

    violations: list[str] = []
    for path in _iter_production_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        violations.extend(find_bypasses(rel, path.read_text(encoding="utf-8")))
    assert violations == []


def test_block_sets_child_columns_is_exact_not_substring_of_something_else() -> None:
    """Sanity check on the column-list matcher itself: a block that merely
    mentions an unrelated column doesn't false-positive."""
    assert not block_sets_child_columns("order_id, user_id, symbol, quantity")
    assert block_sets_child_columns("order_id, parent_order_id")
    assert block_sets_child_columns("order_id, algo_run_id")
    assert find_insert_orders_blocks("no insert here") == []


def _parent(
    *,
    status: OrderStatus = OrderStatus.ACKNOWLEDGED,
    quantity: Decimal = Decimal("10"),
    committed_child_qty: Decimal = Decimal("0"),
    version: int = 3,
) -> OrderView:
    now = datetime.now(timezone.utc)
    return OrderView(
        order_id=uuid4(),
        tenant_id=uuid4(),
        execution_id=None,
        client_order_id="parent-1",
        exchange_order_id=None,
        symbol="BTC/USDT",
        venue_symbol=None,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force="GTC",
        quantity=quantity,
        price=None,
        status=status,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        fee_total=None,
        fee_currency=None,
        version=version,
        parent_order_id=None,
        algo_run_id=uuid4(),
        committed_child_qty=committed_child_qty,
        unknown_since=None,
        provider_order_date=None,
        created_at=now,
        updated_at=now,
    )


async def test_reserve_child_slice_rejects_over_commit() -> None:
    """EM-A1 -- a slice that would push committed_child_qty past parent
    quantity is rejected before anything is written (no submit_order call
    happens, no DB write happens)."""
    parent = _parent(quantity=Decimal("10"), committed_child_qty=Decimal("7"))
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    conn = object()

    with pytest.raises(AlgoConstraintError):
        await reserve_child_slice(
            repo, conn, parent_order_id=parent.order_id, new_slice_qty=Decimal("4")
        )
    repo.set_committed_child_qty.assert_not_awaited()


async def test_reserve_child_slice_rejects_terminal_parent() -> None:
    """EM-A4 -- no new child slice may be reserved against a terminal parent."""
    parent = _parent(status=OrderStatus.FILLED)
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    conn = object()

    with pytest.raises(ParentTerminalError):
        await reserve_child_slice(
            repo, conn, parent_order_id=parent.order_id, new_slice_qty=Decimal("1")
        )
    repo.set_committed_child_qty.assert_not_awaited()


async def test_reserve_child_slice_accepts_boundary_and_commits_exact_sum() -> None:
    """Equality is the accepted boundary (committed + slice == parent qty)
    -- confirms the wiring calls through to the repository with the summed
    value, not just that it doesn't raise."""
    parent = _parent(quantity=Decimal("10"), committed_child_qty=Decimal("6"))
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    repo.set_committed_child_qty.return_value = parent.model_copy(
        update={"committed_child_qty": Decimal("10")}
    )
    conn = object()

    result = await reserve_child_slice(
        repo, conn, parent_order_id=parent.order_id, new_slice_qty=Decimal("4")
    )

    repo.set_committed_child_qty.assert_awaited_once_with(
        conn,
        parent_order_id=parent.order_id,
        expected_version=parent.version,
        committed_child_qty=Decimal("10"),
    )
    assert result.committed_child_qty == Decimal("10")
