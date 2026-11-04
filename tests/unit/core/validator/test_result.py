"""Coverage for src/core/validator/result.py (ValidationResult)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.validator.result import ValidationResult
from src.core.validator.result import ValidationResult as VR
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync


def test_valid_construction_defaults_errors_to_empty_list() -> None:
    result = ValidationResult(is_valid=True)

    assert result.is_valid is True
    assert result.errors == []


def test_valid_construction_with_explicit_errors() -> None:
    result = ValidationResult(is_valid=False, errors=["bad price", "bad qty"])

    assert result.is_valid is False
    assert result.errors == ["bad price", "bad qty"]


def test_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ValidationResult()

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("is_valid",) and e["type"] == "missing" for e in errors)


def test_non_boolean_is_valid_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ValidationResult(is_valid="not-a-bool")

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("is_valid",) for e in errors)


def test_non_list_errors_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ValidationResult(is_valid=True, errors="not-a-list")

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("errors",) for e in errors)


def test_non_string_error_item_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ValidationResult(is_valid=True, errors=[123])

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("errors", 0) for e in errors)


def test_default_factory_failure_is_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected failure in the errors default_factory must surface, not be swallowed."""

    def boom() -> list[str]:
        raise RuntimeError("dependency failure")

    original_factory = ValidationResult.model_fields["errors"].default_factory
    monkeypatch.setattr(ValidationResult.model_fields["errors"], "default_factory", boom)
    ValidationResult.model_rebuild(force=True)
    try:
        with pytest.raises(RuntimeError, match="dependency failure"):
            ValidationResult(is_valid=True)
    finally:
        ValidationResult.model_fields["errors"].default_factory = original_factory
        ValidationResult.model_rebuild(force=True)


@pytest.mark.perf
def test_construction_perf_p95_under_budget(perf_budget: PerfBudget) -> None:
    """Simple pydantic model construction must stay well under the 1ms p95 budget.

    Uses best_of(5) to pick the minimum of 5 samples, mitigating Windows
    quantization tick (~15.6 ms) that would otherwise make a single sample
    read 0 ms or 15.6 ms. The budget/assertion numbers are unchanged.
    """

    def construct() -> None:
        ValidationResult(is_valid=True, errors=["x"])

    best = perf_budget.best_of(construct, n=5, batch=10)
    assert best.cpu_ms < 1.0, (
        f"best-of-5 p95 construction time {best.cpu_ms:.4f}ms exceeded 1ms budget"
    )


def test_construction_perf_p95_under_budget_delay_injection(perf_budget: PerfBudget) -> None:
    """Red-gate: inject 10 ms virtual delay (10× budget) per construction → p95 turns red.

    Uses ``virtual_delayed_sync`` from perf_inject so no real sleep/spin is
    executed — the ledger records the delay and ``PerfBudget.sample`` reads
    it back as extra cpu_ms.  No ``# type: ignore`` needed.
    """

    # Wrap the constructor with 10 ms virtual delay (10× the 1 ms budget).
    original_init = VR.__init__

    def delayed_init(self, **kwargs):
        virtual_delayed_sync(lambda: original_init(self, **kwargs), 0.01)()

    VR.__init__ = delayed_init
    try:

        def construct_with_delay() -> None:
            ValidationResult(is_valid=True, errors=["x"])

        samples = perf_budget.samples(construct_with_delay, n=100, batch=10)
        cpu_ms_values = sorted(s.cpu_ms for s in samples)
        p95_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.95) - 1]
        assert p95_ms >= 1.0, f"delay-injected p95={p95_ms:.4f}ms should exceed 1ms budget"
    finally:
        VR.__init__ = original_init
