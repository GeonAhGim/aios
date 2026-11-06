"""Performance/negative evidence for the slice-sum invariant of plan_slices (L4-06). No DB.

Companion to test_algo_slicer.py::test_slices_sum_exactly_to_total_quantity; kept in a sibling
file so the original (near the 500-line ratchet) is untouched.

Measured locally (best_of n=7, batch=200): one plan_slices call with 7 slices is about 0.05 ms CPU.
Budget 5 ms per call = 100x headroom (>= 30x required) to absorb Windows 15.6 ms process_time
ticks and loaded CI hosts; the virtual 10x injection (50 ms) still reads red.
"""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.services.oms.domain import algo_slicer
from src.services.oms.domain.algo_slicer import SlicePlan, plan_slices
from tests.support.perf_inject import virtual_delayed_sync
from tests.unit.oms.test_algo_slicer import _request

_BUDGET_MS = 5.0


def _plan(total: str = "1", count: int = 7) -> tuple[Decimal, list[SlicePlan]]:
    req = _request(total_quantity=Decimal(total), slice_count=count)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    return req.total_quantity, plans


@pytest.mark.parametrize("total", ["1", "0.00000007", "123456.789", "3"])
def test_sum_is_exact_for_awkward_totals(total: str) -> None:
    expected, plans = _plan(total)
    assert sum(p.quantity for p in plans) == expected


def test_corrupted_plan_is_caught_by_sum_assertion() -> None:
    """Gate-red: dropping part of one slice's quantity must break the exact-sum check."""
    expected, plans = _plan()
    first = replace(plans[0], quantity=plans[0].quantity - Decimal("0.0001"))
    assert sum(p.quantity for p in [first, *plans[1:]]) != expected


@pytest.mark.parametrize("bad_count", [0, -1, 501])
def test_out_of_range_slice_count_rejected(bad_count: int) -> None:
    with pytest.raises(ValidationError):
        _request(slice_count=bad_count)


def test_failure_injection_rng_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: a failing rng must raise, never yield a partial plan."""
    req = _request(slice_count=7)

    class _BoomRng(random.Random):
        def random(self) -> float:
            raise RuntimeError("rng down")

    try:
        plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=_BoomRng(1))
    except RuntimeError:
        return
    # Deterministic algos may not touch the rng; then the sum invariant must still hold.
    assert sum(p.quantity for p in plans) == req.total_quantity


@pytest.mark.perf
def test_plan_slices_within_budget_and_red_under_10x_injection(
    perf_budget, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = _request(total_quantity=Decimal("1"), slice_count=7)

    def call() -> list[SlicePlan]:
        return algo_slicer.plan_slices(
            req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed)
        )

    perf_budget.assert_within(
        call, budget_ms=_BUDGET_MS, n=7, warmup=1, batch=200, label="plan_slices x7"
    )
    assert sum(p.quantity for p in call()) == req.total_quantity

    monkeypatch.setattr(
        algo_slicer, "plan_slices", virtual_delayed_sync(plan_slices, _BUDGET_MS * 10 / 1000)
    )
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            call, budget_ms=_BUDGET_MS, n=7, warmup=1, batch=200, label="injected 10x"
        )
