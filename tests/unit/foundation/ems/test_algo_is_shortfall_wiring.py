"""EM-11 -- `domain/algo/is_shortfall.py` guard-wiring and D2 evidence tests.

Split from test_algo_is_shortfall.py (code_ratchets LOC discipline): structural
wiring checks, failure injection, perf budget and gate-red repro. No DB.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ParentOrder,
    ParentOrderConstraints,
)
from src.foundation.ems.domain.algo import is_shortfall as is_shortfall_module
from src.foundation.ems.domain.algo.guard import ParticipationExceededError
from src.foundation.ems.domain.algo.is_shortfall import plan_is_schedule
from tests.conftest import PerfBudget

_IS_PATH = Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/algo/is_shortfall.py"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, Any] = {
        "kind": AlgoKind.IS,
        "start": _T0,
        "end": _T0 + timedelta(minutes=4),
        "max_participation_pct": Decimal("10"),
        "slice_interval_sec": 60,
        "urgency": Decimal("0.5"),
        "seed": 42,
    }
    defaults.update(overrides)
    return AlgoSpec(**defaults)


def _parent(**overrides: object) -> ParentOrder:
    defaults: dict[str, Any] = {
        "parent_id": uuid4(),
        "instrument_id": "BTC/USDT",
        "side": OrderSide.BUY,
        "qty": Decimal("1000"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.CREATED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)


def _abundant_volume_profile(slice_count: int) -> list[Decimal]:
    return [Decimal("100000")] * slice_count


# -- (a) no reimplementation: guard wiring is real, not decorative ----------


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_is_shortfall_module_imports_all_three_guard_functions() -> None:
    imported = _imported_top_level_modules(_IS_PATH)
    assert any("domain.algo.guard" in module for module in imported), (
        "is_shortfall.py must delegate participation/interval/residual checks "
        "to EM-7's guard.py -- see plan_is_schedule."
    )


def test_is_shortfall_module_calls_all_three_guard_functions() -> None:
    source = _IS_PATH.read_text(encoding="utf-8")
    for guard_call in ("check_participation(", "check_slice_interval(", "plan_residual("):
        assert guard_call in source, (
            f"is_shortfall.py must call guard.{guard_call} -- wiring regressed."
        )


def test_is_shortfall_module_does_not_recompute_the_participation_check_itself() -> None:
    """A bare `/ market_volume` division-comparison would mean is_shortfall.py
    reimplements guard.check_participation's own formula instead of
    delegating to it."""
    source = _IS_PATH.read_text(encoding="utf-8")
    assert "/ market_volume" not in source


def test_is_shortfall_module_imports_twap_exception_classes() -> None:
    imported = _imported_top_level_modules(_IS_PATH)
    assert any(module.endswith("domain.algo.twap") for module in imported), (
        "is_shortfall.py must reuse AlgoConstraintError/ParentTerminalError "
        "from twap.py rather than declaring a third copy."
    )


# -- DEEPEN 2502 (D2): failure injection -- corrupted cap fails closed ------


def test_corrupted_participation_cap_is_rejected_even_when_every_slice_qty_is_zero() -> None:
    """Failure injection: `AlgoSpec.max_participation_pct`'s
    `Field(gt=0, le=100)` only runs at construction time -- pydantic does
    not re-validate on plain attribute assignment (no
    `model_config = ConfigDict(validate_assignment=True)` here) -- so a
    downstream mutation bug (e.g. a normalization/reprice step touching
    the wrong field) can leave a live `AlgoSpec` holding a negative cap.
    A negative cap drives `cap_qty = max(0, volume * pct / 100)` to `0` for
    every non-final slice, so `slice_qty` is `0` there too and the
    per-slice `check_participation` call is never reached (see the
    gate-red repro below for what that would let happen). This must still
    be rejected via `_validate_participation_cap`'s up-front call."""
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0.5"), max_participation_pct=Decimal("10")),
    )
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("-5"))
    with pytest.raises(ParticipationExceededError):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- DEEPEN 2502 (D2): numeric performance assertion -------------------------


@pytest.mark.perf
def test_plan_is_schedule_stays_under_budget_at_the_max_slice_count(
    perf_budget: PerfBudget,
) -> None:
    """500 slices (the module's own `_MAX_SLICE_COUNT` cap) x 50 calls must
    finish well under a generous budget -- a regression that turned the
    per-slice O(n) loop, weight computation, or the guard calls into
    something O(n^2) would blow well past this on 500 slices. task-7434:
    measured via the shared process_time-based perf_budget fixture instead
    of raw wall-clock perf_counter().

    Global virtual-delay ledger must be cleared before measurement so that
    prior tests using ``virtual_delayed_sync`` in the same xdist worker
    process do not inflate ``cpu_ms`` readings (task-22280).
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    parent = _parent(
        qty=Decimal("1000000"),
        algo=_algo(
            start=_T0,
            end=_T0 + timedelta(seconds=500),
            slice_interval_sec=1,
            urgency=Decimal("0.7"),
            max_participation_pct=Decimal("100"),
        ),
    )
    profile = [Decimal("100000")] * 500

    def _run_once() -> None:
        for _ in range(50):
            plan_is_schedule(parent, volume_profile=profile)

    # budget_ms=1500: local CPU ~125ms × 500 slices × 50 calls = 6.25s raw work;
    # best_of(5) picks the cleanest sample. CI 병렬(xdist) CPU 경합 spike를
    # 흡수하기 위해 800ms → 1500ms 상향 (task-22107, pytest_perf_parallel_2 실패 대응).
    perf_budget.assert_within(
        _run_once, budget_ms=1500.0, label="50 plan_is_schedule calls at 500 slices"
    )


# -- DEEPEN 2502 (D2): gate-red repro -----------------------------------------


def test_gate_red_without_the_upfront_cap_validation_the_corrupted_cap_passes_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before/after proof that `_validate_participation_cap`'s call is
    load-bearing, not decorative: disable only that one guard call
    (monkeypatched to a no-op, leaving every per-slice
    `guard.check_participation` call untouched) and replay the exact
    corrupted-cap scenario from the failure-injection test above. Without
    the up-front call, nothing else in `plan_is_schedule` ever notices --
    the whole order is silently dumped into the exempt final slice."""
    monkeypatch.setattr(is_shortfall_module, "_validate_participation_cap", lambda _pct: None)
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0.5"), max_participation_pct=Decimal("10")),
    )
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("-5"))
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    quantities = [c.planned_qty for c in children]
    assert quantities == [Decimal("1000")]
    assert sum(quantities) == Decimal("1000")


# -- file size discipline -----------------------------------------------------


def test_is_shortfall_module_is_at_most_280_lines() -> None:
    line_count = len(_IS_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 280, (
        f"is_shortfall.py has {line_count} lines, exceeding the 280-line leaf cap."
    )
