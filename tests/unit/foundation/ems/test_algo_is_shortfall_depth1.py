"""EM-11 -- `domain/algo/is_shortfall.py` depth-1 D2 evidence (DEEPEN-DEPTH EM-11).

Supplements ``test_algo_is_shortfall.py`` (depth-1).  Adds the missing
D2 categories that the parent file does not cover:

* negative tests ≥ 3 (urgency out-of-range, zero/negative qty,
  ``max_participation_pct > 100``)
* failure-injection: corrupt ``max_participation_pct`` after construction
  → ``ParticipationExceededError`` via ``_validate_participation_cap``
* gate-red + perf: 10× virtual-delay injection on ``plan_is_schedule``
  must exceed the ``PerfBudget`` budget (``best_of`` measurement)
* adversarial: urgency boundary values (−1, 2) and weight monotonicity

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-11 DoD
"""

from __future__ import annotations

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
from src.foundation.ems.domain.algo.guard import ParticipationExceededError
from src.foundation.ems.domain.algo.is_shortfall import (
    AlgoConstraintError,
    _urgency_weights,
    plan_is_schedule,
)
from src.foundation.ems.domain.algo.twap import (
    AlgoConstraintError as TwapAlgoConstraintError,
)
from tests.support.perf_budget import PerfBudget

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


# -- (a) negative: urgency out of [0, 1] boundaries -------------------------


def test_urgency_minus_one_is_rejected() -> None:
    """urgency < 0 is not a valid urgency value — pydantic Field
    ``ge=0`` should reject it at construction."""
    with pytest.raises(ValueError, match="urgency"):
        _algo(urgency=Decimal("-1"))


def test_urgency_two_is_rejected() -> None:
    """urgency > 1 is not a valid urgency value — pydantic Field
    ``le=1`` should reject it at construction."""
    with pytest.raises(ValueError, match="urgency"):
        _algo(urgency=Decimal("2"))


def test_zero_qty_parent_is_rejected() -> None:
    """A parent with qty ≤ 0 must not produce a plan.
    Pydantic ``ParentOrder`` validates ``gt=0`` on qty at construction."""
    with pytest.raises(ValueError, match="qty"):
        _parent(qty=Decimal("0"))


def test_negative_qty_parent_is_rejected() -> None:
    """A parent with negative qty must not produce a plan.
    Pydantic ``ParentOrder`` validates ``gt=0`` on qty at construction."""
    with pytest.raises(ValueError, match="qty"):
        _parent(qty=Decimal("-100"))


def test_max_participation_pct_over_100_is_rejected() -> None:
    """max_participation_pct > 100 violates the pydantic Field ``le=100``."""
    with pytest.raises(ValueError, match="max_participation_pct"):
        _algo(max_participation_pct=Decimal("101"))


# -- (b) failure injection: corrupt cap after construction -------------------


def test_failure_inject_negative_cap_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutate ``max_participation_pct`` to a negative value *after* the
    ``AlgoSpec`` is constructed, bypassing pydantic validation.  The
    upfront ``_validate_participation_cap`` call in ``plan_is_schedule``
    must catch it via ``check_participation``'s fail-closed cap-range
    check, raising ``ParticipationExceededError``.

    This is a failure-injection test: the production code path that
    protects against post-construction mutation is exercised here.
    """
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0.5"), max_participation_pct=Decimal("10")),
    )
    # Corrupt the cap after construction — pydantic already ran.
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("-5"))
    with pytest.raises(ParticipationExceededError):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- (c) gate-red + perf: 10× virtual-delay injection exceeds budget ---------


@pytest.mark.perf
def test_gate_red_with_10x_virtual_delay_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """Inject 10× virtual delay (via ``perf_inject.virtual_delayed_sync``)
    into ``plan_is_schedule`` and assert the measured time exceeds the
    budget.  This is a gate-red reproduction: the budget was set against
    the real (undelayed) single-call CPU time, so a 10× injection must
    blow past it.

    Uses ``best_of`` directly (not ``assert_within``) so the test can
    assert the value exceeds the budget rather than letting
    ``assert_within`` raise on the first sample.
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()

    # Patch the inner loop's ``check_participation`` to add 0.05s virtual
    # delay per call (called once per non-final slice = 3 times for a
    # 4-slice plan → ~150 ms total injected).
    import src.foundation.ems.domain.algo.is_shortfall as mod

    original_check = mod.check_participation  # type: ignore[attr-defined]
    mod.check_participation = virtual_delayed_sync(original_check, 0.05)  # type: ignore[attr-defined]

    try:
        parent = _parent(
            qty=Decimal("10000"),
            algo=_algo(
                start=_T0,
                end=_T0 + timedelta(seconds=240),
                urgency=Decimal("0.7"),
                max_participation_pct=Decimal("10"),
            ),
        )
        profile = [Decimal("100000")] * 4

        # best_of(10): many samples, take the minimum to reduce xdist
        # noise.  The injected delay (3 slices × 50 ms = 150 ms) must
        # exceed the 5 ms budget — this is the gate-red assertion.
        sample = perf_budget.best_of(
            lambda: plan_is_schedule(parent, volume_profile=profile),
            n=10,
        )
        assert sample.cpu_ms > Decimal("5.0"), (
            f"Expected gate-red to trip (delayed call returned {sample.cpu_ms} ms, "
            "budget 5 ms) — the injected virtual delay should have been measured."
        )
    finally:
        # Restore original function.
        mod.check_participation = original_check  # type: ignore[attr-defined]


# -- (d) adversarial: weight monotonicity at boundaries ----------------------


def test_urgency_weights_are_non_increasing_at_urgency_1() -> None:
    """At urgency=1 the weight sequence is [n, n-1, ..., 1] — strictly
    decreasing."""
    weights = _urgency_weights(Decimal("1"), 5)
    assert weights == [Decimal("5"), Decimal("4"), Decimal("3"), Decimal("2"), Decimal("1")]
    for w in weights:
        assert w > Decimal("0")


def test_urgency_weights_are_all_1_at_urgency_0() -> None:
    """At urgency=0 every weight is 1 — flat TWAP."""
    weights = _urgency_weights(Decimal("0"), 5)
    assert weights == [Decimal("1")] * 5


def test_urgency_weights_are_monotone_nondecreasing_in_urgency_for_each_index() -> None:
    """For any fixed index i, weight[i] is non-decreasing in urgency.
    Earlier slices get relatively more weight at higher urgency."""
    slice_count = 10
    urgency_grid = [Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")]
    for index in range(slice_count):
        weights_at_index = [_urgency_weights(u, slice_count)[index] for u in urgency_grid]
        for lower, higher in zip(weights_at_index, weights_at_index[1:]):  # noqa: B905
            assert lower <= higher


def test_weight_sum_is_consistent_for_any_urgency() -> None:
    """sum(weight[i]) = (1-urgency)*n + urgency * n*(n+1)/2 — verify for
    several urgencies."""
    slice_count = 5
    for urgency in [Decimal("0"), Decimal("0.5"), Decimal("1")]:
        weights = _urgency_weights(urgency, slice_count)
        total = sum(weights, Decimal("0"))
        n = Decimal(str(slice_count))
        expected = (Decimal("1") - urgency) * n + urgency * n * (n + Decimal("1")) / Decimal("2")
        assert total == expected


# -- (e) negative: exception class reuse -------------------------------------


def test_is_shortfall_exceptions_are_twap_classes() -> None:
    """Confirm the re-exported exceptions are the same objects as twap's."""
    assert AlgoConstraintError is TwapAlgoConstraintError
    assert AlgoConstraintError.__module__ == "src.foundation.ems.domain.algo.twap"
