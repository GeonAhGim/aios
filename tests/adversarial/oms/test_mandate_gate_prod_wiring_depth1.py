"""R-59 depth-1 evidence for the I-09 `require_mandate` wiring scanner.

Adds (without touching test_mandate_gate_prod_wiring.py): extra negative cases,
failure injection, a gate-red reproduction, and a perf budget.

Perf budget: `_scan_source` over 3000 noise lines + 1 gate call measured
~29 ms wall (15.6 ms CPU tick) (best-of-5, local Windows dev box, 2026-11-13); the budget
`_SCAN_BUDGET_MS` leaves >=30x headroom over that figure. The 10x virtual-delay
injection must turn the same assertion red.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.adversarial.oms.test_mandate_gate_prod_wiring import (
    _scan_file,
    _scan_source,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_TODAY = date(2026, 1, 1)
_SCAN_BUDGET_MS = 1000.0


def _oks(source: str) -> list[bool]:
    return [f.ok for f in _scan_source(source, "fixture.py", today=_TODAY)]


@pytest.mark.parametrize(
    "arg",
    ["require_mandate=1", "require_mandate='True'", "require_mandate=flag", "require_mandate=None"],
)
def test_negative_non_literal_true_is_flagged(arg: str) -> None:
    assert _oks(f"make_foundation_pre_submit_gate(pool, {arg})\n") == [False]


def test_negative_omitted_keyword_is_flagged() -> None:
    assert _oks("make_foundation_pre_submit_gate(pool)\n") == [False]


def test_negative_one_bad_call_among_good_ones_is_flagged() -> None:
    source = (
        "make_foundation_pre_submit_gate(a, require_mandate=True)\n"
        "make_foundation_pre_submit_gate(b, require_mandate=False)\n"
        "make_foundation_pre_submit_gate(c, require_mandate=True)\n"
    )
    assert _oks(source) == [True, False, True]


def test_negative_adr_exception_with_wrong_shape_is_flagged() -> None:
    source = (
        "# ADR-EXCEPTION: ADR-X require_mandate=True expires=2099-01-01\n"
        "make_foundation_pre_submit_gate(pool, require_mandate=False)\n"
    )
    assert _oks(source) == [False]


def test_failure_injection_syntax_error_fails_closed() -> None:
    with pytest.raises(SyntaxError):
        _scan_source(
            "make_foundation_pre_submit_gate(pool, require_mandate=\n", "x.py", today=_TODAY
        )


def test_failure_injection_directory_target_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _scan_file(tmp_path, today=_TODAY)


def test_gate_red_broken_input_is_caught_where_good_input_passes() -> None:
    good = "make_foundation_pre_submit_gate(pool, require_mandate=True)\n"
    broken = good.replace("True", "False")
    assert _oks(good) == [True]
    assert _oks(broken) == [False]


def _big_source() -> str:
    noise = "\n".join(f"unrelated_call_{i}(x, y, z)" for i in range(3000))
    return f"{noise}\nmake_foundation_pre_submit_gate(pool, require_mandate=True)\n"


def _scan_big() -> list[bool]:
    return _oks(_big_source())


@pytest.mark.perf
def test_perf_scan_within_budget(perf_budget: PerfBudget) -> None:
    reset_virtual_totals()
    sample = perf_budget.best_of(_scan_big, n=5, warmup=1)
    assert sample.result == [True]
    assert sample.cpu_ms < _SCAN_BUDGET_MS, perf_budget.describe(sample, budget_ms=_SCAN_BUDGET_MS)


@pytest.mark.perf
def test_perf_scan_gate_red_with_10x_injection(perf_budget: PerfBudget) -> None:
    reset_virtual_totals()
    slow = virtual_delayed_sync(_scan_big, _SCAN_BUDGET_MS * 10 / 1000)
    sample = perf_budget.best_of(slow, n=5, warmup=1)
    assert not (sample.cpu_ms < _SCAN_BUDGET_MS)
    reset_virtual_totals()
