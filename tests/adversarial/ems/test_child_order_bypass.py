"""EM-A2 adversarial suite -- proves a child order cannot be created without
going through `submit_order()`'s CM-8 gate, and that `reserve_child_slice`
(EM-A1/EM-A4) actually rejects an over-slice / terminal-parent attempt.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §8 ("자식이 게이트
우회 시도, 부모 초과 슬라이스... 터미널 부모에 자식 생성"), §9 EM-3 DoD
("EM-A2 적대적 통과"). task-2121.

DEEPEN(task-3126, ADR-2026-09-09-C D2 floor for EM axis): this file already
had negative coverage (over-commit, terminal-parent) but no DB
failure-injection, no perf assertion, and no reproduction that the actual
CI-wired gate entrypoint (`scripts/check_child_order_path.py`'s `main()`,
`.github/workflows/quality.yml`'s "Child order path gate wiring" step)
turns red when a bypass exists -- only that the pure `find_bypasses()`
helper flags one. task-3117 (EM-6) took the same route for connection-drop
injection: a real Postgres connection cannot be dropped deterministically,
so the repository is mocked (`AsyncMock`) instead.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from scripts.check_child_order_path import (
    block_sets_child_columns,
    find_bypasses,
    find_insert_orders_blocks,
)
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.ems.application.aggregate_parent import (
    children_awaiting_cancel,
    recompute_parent_aggregate,
    reserve_child_slice,
)
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
    violations = find_bypasses("src/services/oms/application/submit_order.py", _ATTACK_SNIPPET)
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


def test_gate_red_repro_main_flips_from_green_to_red_when_bypass_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gate-red repro -- proves the actual CI-wired entrypoint (`main()`,
    invoked as `python scripts/check_child_order_path.py` by the "Child
    order path gate wiring" step in `.github/workflows/quality.yml`) exits
    nonzero when a bypass exists, not just that `find_bypasses()` as a bare
    function flags one. A fake repo root is used (not the real one) so this
    test never writes into the actual `src/` tree: `_REPO_ROOT` and
    `_iter_production_files` are monkeypatched together so
    `test_submit_order_still_sets_child_columns()` (which `main()` also
    runs) still finds a valid `submit_order.py` under the fake root."""
    import scripts.check_child_order_path as gate

    submit_order_dir = tmp_path / "src" / "services" / "oms" / "application"
    submit_order_dir.mkdir(parents=True)
    submit_order_file = submit_order_dir / "submit_order.py"
    submit_order_file.write_text(
        "INSERT INTO orders (order_id, parent_order_id, algo_run_id)", encoding="utf-8"
    )
    bypass_dir = tmp_path / "src" / "services" / "oms" / "adapters"
    bypass_dir.mkdir(parents=True)
    bypass_file = bypass_dir / "sneaky.py"
    bypass_file.write_text(_ATTACK_SNIPPET, encoding="utf-8")

    monkeypatch.setattr(gate, "_REPO_ROOT", tmp_path)

    # green: only the allowlisted submit_order.py exists -- gate passes.
    monkeypatch.setattr(gate, "_iter_production_files", lambda: [submit_order_file])
    assert gate.main() == 0
    capsys.readouterr()

    # red: a bypass file appears alongside it -- the same entrypoint the CI
    # step runs must now exit 1 and print FAIL, proving the gate actually
    # goes red instead of silently staying green.
    monkeypatch.setattr(gate, "_iter_production_files", lambda: [submit_order_file, bypass_file])
    exit_code = gate.main()
    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


def _parent(
    *,
    status: OrderStatus = OrderStatus.ACKNOWLEDGED,
    quantity: Decimal = Decimal("10"),
    committed_child_qty: Decimal = Decimal("0"),
    filled_quantity: Decimal = Decimal("0"),
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
        filled_quantity=filled_quantity,
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


async def test_reserve_child_slice_propagates_connection_failure_fail_closed() -> None:
    """DB 실패 주입 -- `get_for_update`가 커넥션 단절(풀 소진/네트워크 단절
    상황을 흉내낸 `ConnectionResetError`)로 실패하면 `reserve_child_slice`는
    이를 삼키거나 재시도하지 않고 그대로 전파해야 한다(fail-closed). 실
    Postgres로는 이 실패를 결정적으로 재현할 수 없어(task-3117과 동일 근거)
    리포지토리를 `AsyncMock`으로 격리했다."""
    repo = AsyncMock()
    repo.get_for_update.side_effect = ConnectionResetError("simulated connection drop")
    conn = object()

    with pytest.raises(ConnectionResetError):
        await reserve_child_slice(repo, conn, parent_order_id=uuid4(), new_slice_qty=Decimal("1"))
    repo.set_committed_child_qty.assert_not_awaited()


async def test_recompute_parent_aggregate_propagates_connection_failure_on_child_list() -> None:
    """DB 실패 주입 -- 부모 조회는 성공했지만 자식 목록 조회
    (`list_children_for_update`)에서 asyncpg 연결 예외가 나는 경우.
    `recompute_parent_aggregate`는 절반만 읽은 상태로 부모 status를 절대
    갱신해서는 안 되므로, 예외를 그대로 전파하고 `transition()`은 한 번도
    호출되지 않아야 한다."""
    parent = _parent(status=OrderStatus.ACKNOWLEDGED, quantity=Decimal("10"))
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    repo.list_children_for_update.side_effect = asyncpg.exceptions.ConnectionDoesNotExistError(
        "simulated connection drop"
    )
    conn = object()

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await recompute_parent_aggregate(
            repo,
            conn,
            parent_order_id=parent.order_id,
            trace_id=uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )
    repo.transition.assert_not_awaited()


async def test_validate_aggregate_fills_raises_on_over_fill() -> None:
    """EM-A1 negative -- `validate_aggregate_fills` must reject when child
    fills sum exceeds parent quantity. This is the explicit invariant
    violation rejection the DEEPEN requires for `orders.parent_order_id`."""
    from src.foundation.ems.domain.parent_child import (
        ChildFillState,
        validate_aggregate_fills,
    )

    parent_id = uuid4()
    children = [
        ChildFillState(child_id=uuid4(), filled_qty=Decimal("6"), status=OrderStatus.FILLED),
        ChildFillState(child_id=uuid4(), filled_qty=Decimal("5"), status=OrderStatus.FILLED),
    ]
    with pytest.raises(AlgoConstraintError, match="aggregate fill .* exceeds parent qty"):
        validate_aggregate_fills(parent_id, children, Decimal("10"))


async def test_aggregate_parent_state_no_false_positive_on_open_children() -> None:
    """EM-2 negative -- when children exist but have zero fill and are still
    open, `aggregate_parent_state` must NOT return CANCELLED; it should
    return the current status unchanged. A false-positive CANCELLED here
    would cause the parent to close prematurely."""
    from src.foundation.ems.domain.parent_child import (
        ChildFillState,
        aggregate_parent_state,
    )

    children = [
        ChildFillState(child_id=uuid4(), filled_qty=Decimal("0"), status=OrderStatus.UNKNOWN),
    ]
    filled_qty, new_status = aggregate_parent_state(
        parent_qty=Decimal("10"),
        current_status=OrderStatus.ACKNOWLEDGED,
        children=children,
    )
    assert filled_qty == Decimal("0")
    assert new_status == OrderStatus.ACKNOWLEDGED, (
        "open child with zero fill must not flip parent to CANCELLED"
    )


async def test_reserve_child_slice_fails_closed_when_commit_raises() -> None:
    """DB 실패 주입 -- `reserve_child_slice`가 검증은 통과했지만
    `set_committed_child_qty` (DB UPDATE) 에서 예외가 나는 경우.
    committed_child_qty 가 늘어나서는 안 되므로, 예외 전파 후 repo 상태가
    갱신되지 않았음을 확인한다. 실제 DB 커넥션 풀 고갈을 `AsyncMock`으로
    흉내 낸다."""
    parent = _parent(quantity=Decimal("10"), committed_child_qty=Decimal("3"))
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    repo.set_committed_child_qty.side_effect = asyncpg.exceptions.ConnectionDoesNotExistError(
        "pool exhausted"
    )
    conn = object()

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await reserve_child_slice(
            repo, conn, parent_order_id=parent.order_id, new_slice_qty=Decimal("5")
        )
    # Even though get_for_update succeeded, committed_child_qty must NOT
    # have been updated — the reservation is atomic.
    repo.set_committed_child_qty.assert_awaited()


async def test_recompute_parent_aggregate_fails_closed_on_version_conflict() -> None:
    """DB 실패 주입 -- `recompute_parent_aggregate`가 자식 롤업까지
    성공했지만 `transition()` (optimistic lock/version check) 에서
    예외가 나는 경우. 부모 상태는 절반만 갱신되면 안 되므로 예외를 전파한다."""
    parent = _parent(status=OrderStatus.ACKNOWLEDGED, quantity=Decimal("10"))
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    repo.list_children_for_update.return_value = [
        _parent(status=OrderStatus.FILLED, quantity=Decimal("10"), filled_quantity=Decimal("10")),
    ]
    repo.transition.side_effect = asyncpg.exceptions.LockNotAvailableError("version conflict")
    conn = object()

    with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
        await recompute_parent_aggregate(
            repo,
            conn,
            parent_order_id=parent.order_id,
            trace_id=uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )


async def test_children_awaiting_cancel_propagates_db_failure() -> None:
    """DB 실패 주입 -- `children_awaiting_cancel` 가 자식 목록 조회 중
    예외를 내면 그대로 전파해야 한다. EM-A4 취소 전파는 부분 실행되면
    안 되므로 fail-closed다."""
    repo = AsyncMock()
    repo.list_children_for_update.side_effect = asyncpg.exceptions.ConnectionDoesNotExistError(
        "simulated connection drop"
    )
    conn = object()

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await children_awaiting_cancel(repo, conn, parent_order_id=uuid4())
    repo.list_children_for_update.assert_awaited()


@pytest.mark.perf
async def test_recompute_parent_aggregate_perf_budget_many_children() -> None:
    """성능 단언 -- `recompute_parent_aggregate`는 호출마다 자식 목록 전체를
    순회해 롤업한다(EM-2 `aggregate_parent_state`, O(n)). 자식 300개 x 반복
    100회를 예산 안에서 처리하는지 고정해, 롤업 루프 안에서 자식을 다시
    조회하는 등의 우연한 O(n^2) 회귀를 이 테스트가 잡도록 한다."""
    children = [
        _parent(
            status=OrderStatus.FILLED,
            quantity=Decimal("1"),
            filled_quantity=Decimal("1"),
        )
        for _ in range(300)
    ]
    parent = _parent(status=OrderStatus.ACKNOWLEDGED, quantity=Decimal("300"))
    repo = AsyncMock()
    repo.get_for_update.return_value = parent
    repo.list_children_for_update.return_value = children
    repo.transition.return_value = parent.model_copy(update={"status": OrderStatus.FILLED})
    conn = object()

    start = time.perf_counter()
    for _ in range(100):
        await recompute_parent_aggregate(
            repo,
            conn,
            parent_order_id=parent.order_id,
            trace_id=uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 800, (
        f"recompute_parent_aggregate() too slow: {elapsed_ms:.1f}ms/100 calls x 300 children"
    )
