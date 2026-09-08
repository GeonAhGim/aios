"""EM-7 -- `domain/algo/guard.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-7 DoD (a)-(g).
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.foundation.ems.domain.algo.guard import (
    ParticipationExceededError,
    ResidualOverflowError,
    SliceIntervalError,
    check_participation,
    check_slice_interval,
    plan_residual,
)

_GUARD_PATH = Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/algo/guard.py"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


# -- (a) participation cap: boundary + fail-closed on zero volume ----------


def test_participation_at_the_cap_passes() -> None:
    check_participation(Decimal("100"), Decimal("1000"), Decimal("10"))


def test_participation_one_unit_over_the_cap_is_rejected() -> None:
    with pytest.raises(ParticipationExceededError, match="exceeding the cap"):
        check_participation(Decimal("101"), Decimal("1000"), Decimal("10"))


def test_participation_one_unit_under_the_cap_passes() -> None:
    check_participation(Decimal("99"), Decimal("1000"), Decimal("10"))


def test_zero_market_volume_is_rejected_without_raising_zero_division() -> None:
    with pytest.raises(ParticipationExceededError, match="market_volume must be > 0"):
        check_participation(Decimal("1"), Decimal("0"), Decimal("10"))


# -- (b) slice interval: boundary + tz-aware only ---------------------------


def test_slice_interval_one_second_short_is_rejected() -> None:
    with pytest.raises(SliceIntervalError, match="shorter than"):
        check_slice_interval(_T0, _T0 + timedelta(seconds=59), 60)


def test_slice_interval_exactly_at_spec_passes() -> None:
    check_slice_interval(_T0, _T0 + timedelta(seconds=60), 60)


def test_slice_interval_one_second_longer_passes() -> None:
    check_slice_interval(_T0, _T0 + timedelta(seconds=61), 60)


def test_naive_datetime_is_rejected() -> None:
    naive = datetime(2026, 1, 1)
    with pytest.raises(SliceIntervalError, match="tz-aware"):
        check_slice_interval(naive, _T0 + timedelta(seconds=60), 60)


# -- (c) residual close-out: exact match, never negative, overflow rejected -


def test_plan_residual_matches_parent_minus_filled_minus_scheduled() -> None:
    residual = plan_residual(Decimal("100"), Decimal("30"), Decimal("20"))
    assert residual == Decimal("50")


def test_plan_residual_is_never_negative_at_exact_exhaustion() -> None:
    residual = plan_residual(Decimal("100"), Decimal("60"), Decimal("40"))
    assert residual == Decimal("0")


def test_plan_residual_overflow_is_rejected() -> None:
    with pytest.raises(ResidualOverflowError, match="exceeds parent_qty"):
        plan_residual(Decimal("100"), Decimal("60"), Decimal("41"))


# -- (d) EM-A2 fail-closed: no input widens the participation cap ----------


@pytest.mark.parametrize("adversarial_cap", [None, Decimal("-5"), Decimal("101")])
def test_no_participation_cap_input_is_ever_read_as_unlimited(
    adversarial_cap: Decimal | None,
) -> None:
    with pytest.raises(ParticipationExceededError, match="never"):
        check_participation(Decimal("1"), Decimal("1000"), adversarial_cap)


# -- (e) EM-A3 determinism/purity -------------------------------------------


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module.split(".")[0])
    return found


def test_guard_module_imports_no_nondeterministic_source() -> None:
    found = _imported_top_level_modules(_GUARD_PATH) & {"time", "random", "uuid"}
    assert not found, f"guard.py: EM-A3 violation -- forbidden import found: {found}"


def test_guard_module_never_reads_the_wall_clock() -> None:
    tree = ast.parse(_GUARD_PATH.read_text(encoding="utf-8"))
    now_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "today"}
    ]
    assert not now_calls, "guard.py: EM-A3 violation -- wall-clock read found (datetime.now/...)"


def test_same_inputs_produce_the_same_result_on_repeated_calls() -> None:
    first = plan_residual(Decimal("100"), Decimal("30"), Decimal("20"))
    second = plan_residual(Decimal("100"), Decimal("30"), Decimal("20"))
    assert first == second

    check_participation(Decimal("100"), Decimal("1000"), Decimal("10"))
    check_participation(Decimal("100"), Decimal("1000"), Decimal("10"))  # no state, no raise


# -- (g) file size discipline ------------------------------------------------


def test_guard_module_is_at_most_300_lines() -> None:
    line_count = len(_GUARD_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 300, f"guard.py has {line_count} lines, exceeding the 300-line leaf cap."
