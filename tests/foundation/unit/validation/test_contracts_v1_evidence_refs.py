"""task-4972 -- `ValidationResultView.evidence_refs` contract-drift fix.

`domain/check_result.py`'s `CheckResult.evidence_refs` (and
`domain/models.ValidationResult.evidence_refs`, persisted by migration M3)
had no matching field on the wire view, and `start_validation._run_to_view`
dropped `ValidationResult.evidence_refs` on the floor even after the field
was added to the contract (task-3358/L37) -- the frontend parser then
flagged the contract as drifted (`evidence_refs` extra/missing across the
three shared-types drift guards). D2 evidence (ADR-2026-09-09-C Decision 1):
negative >=3, failure injection 1, perf assertion 1, gate-red repro 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.validation.application.start_validation import _run_to_view
from src.foundation.validation.contracts.v1 import SCHEMA_VERSION, ValidationResultView
from src.foundation.validation.domain.models import (
    Outcome,
    RunState,
    ValidationResult,
    ValidationRun,
)
from tests.support.perf_budget import PerfBudget


def _run(**overrides) -> ValidationRun:
    kwargs = dict(
        id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
        check_type="backtest",
        input_snapshot_hash="deadbeef",
        cost_model={"fee_bps": "10"},
        warmup_bars=20,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
        state=RunState.SUCCEEDED,
        created_at=datetime.now(UTC),
    )
    kwargs.update(overrides)
    return ValidationRun(**kwargs)


def _result(**overrides) -> ValidationResult:
    kwargs = dict(
        id=uuid4(),
        run_id=uuid4(),
        outcome=Outcome.PASS,
        metrics={"sharpe": 1.2},
        result_hash="hash-1",
        created_at=datetime.now(UTC),
    )
    kwargs.update(overrides)
    return ValidationResult(**kwargs)


def test_evidence_refs_defaults_to_empty_list() -> None:
    view = ValidationResultView(
        run_id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
        check_type="backtest",
        state="SUCCEEDED",
        outcome=None,
        metrics=None,
        warnings=[],
        hard_fail_reasons=[],
        obligations=[],
        result_hash=None,
        created_at=datetime.now(UTC),
    )
    assert view.evidence_refs == []


def test_schema_version_unchanged_by_additive_field() -> None:
    # 107 §3.3 MINOR rule -- an optional, defaulted additive field is
    # backward compatible and does not bump SCHEMA_VERSION.
    assert SCHEMA_VERSION == "v1"


def test_run_to_view_propagates_result_evidence_refs() -> None:
    # Gate-red repro: before this fix, `_run_to_view` never read
    # `result.evidence_refs`, so a completed run with recorded evidence
    # silently reported an empty list on the wire (I-xx provenance-chain
    # absolute-principle violation) -- this is the exact CI-red repro.
    run = _run()
    result = _result(evidence_refs=("snapshot:abc123", "artifact:def456"))
    view = _run_to_view(run, result)
    assert view.evidence_refs == ["snapshot:abc123", "artifact:def456"]


def test_run_to_view_with_no_result_yields_empty_evidence_refs() -> None:
    run = _run(state=RunState.RUNNING)
    view = _run_to_view(run, None)
    assert view.evidence_refs == []


def test_negative_evidence_refs_must_be_a_list() -> None:
    with pytest.raises(ValidationError):
        ValidationResultView(
            run_id=uuid4(),
            strategy_id="strat-1",
            strategy_version="v1",
            check_type="backtest",
            state="SUCCEEDED",
            outcome=None,
            metrics=None,
            warnings=[],
            hard_fail_reasons=[],
            obligations=[],
            result_hash=None,
            created_at=datetime.now(UTC),
            evidence_refs="not-a-list",
        )


def test_negative_evidence_refs_items_must_be_strings() -> None:
    with pytest.raises(ValidationError):
        ValidationResultView(
            run_id=uuid4(),
            strategy_id="strat-1",
            strategy_version="v1",
            check_type="backtest",
            state="SUCCEEDED",
            outcome=None,
            metrics=None,
            warnings=[],
            hard_fail_reasons=[],
            obligations=[],
            result_hash=None,
            created_at=datetime.now(UTC),
            evidence_refs=[123],
        )


def test_negative_evidence_refs_none_rejected() -> None:
    # `None` is not the same as "no evidence" (that's `[]`) -- an explicit
    # None must fail closed rather than silently coerce.
    with pytest.raises(ValidationError):
        ValidationResultView(
            run_id=uuid4(),
            strategy_id="strat-1",
            strategy_version="v1",
            check_type="backtest",
            state="SUCCEEDED",
            outcome=None,
            metrics=None,
            warnings=[],
            hard_fail_reasons=[],
            obligations=[],
            result_hash=None,
            created_at=datetime.now(UTC),
            evidence_refs=None,
        )


@pytest.mark.perf
def test_construction_throughput_p99_under_budget(perf_budget) -> None:
    # Perf assertion (ADR-2026-09-09-C budget table, generic pydantic view
    # construction): p99 for 1000 constructions stays well under 5ms/call.
    # batch=8 reduces Windows process_time() quantization error from ~15.6ms
    # to ~2ms per call (tick/batch), preventing 0-convergence on fast paths.
    run = _run()
    result = _result(evidence_refs=("snapshot:abc123",))

    samples = perf_budget.samples(
        lambda: _run_to_view(run, result),
        n=1000,
        batch=8,
    )
    cpu_ms = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_ms[int(len(cpu_ms) * 0.99)]
    assert p99_ms < 5.0


@pytest.mark.perf
def test_construction_gate_red_fails_under_cpu_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction: injecting 50ms CPU delay (10x the 5ms budget)
    via delayed_sync into _run_to_view proves the p99 assertion fires on
    real slowdown — confirming the test has detection power, not a
    trivially-passing zero. batch=8 matches the normal perf test to avoid
    Windows process_time() quantization (~15.6ms tick) skewing results."""
    from tests.support.perf_inject import delayed_sync

    original_run_to_view = _run_to_view

    def slow_run_to_view(
        run: ValidationRun, result: ValidationResult | None
    ) -> ValidationResultView:
        return delayed_sync(lambda: original_run_to_view(run, result), 0.05)()

    # Patch the local import binding so the lambda below calls the delayed version.
    import sys

    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "_run_to_view", slow_run_to_view)

    run = _run()
    result = _result(evidence_refs=("snapshot:abc123",))

    samples = perf_budget.samples(
        lambda: _run_to_view(run, result),
        n=10,
        batch=8,
    )
    cpu_ms = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_ms[int(len(cpu_ms) * 0.99)]
    # 50ms CPU delay >> 5ms budget — must fail, proving detection works.
    with pytest.raises(AssertionError):
        assert p99_ms < 5.0
