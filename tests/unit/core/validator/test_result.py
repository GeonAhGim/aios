"""Coverage for src/core/validator/result.py (ValidationResult)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.validator.result import ValidationResult
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


def _construct() -> None:
    ValidationResult(is_valid=True, errors=["x"])


@pytest.mark.perf
def test_construction_perf_under_budget(perf_budget: PerfBudget) -> None:
    """Simple pydantic model construction must stay under the 1ms budget.

    Best-of-N with a large batch keeps the Windows process_time quantization tick from
    dominating a single sample.
    """
    sample = perf_budget.best_of(_construct, n=5, batch=200)
    assert sample.cpu_ms < 1.0, f"construction {sample.cpu_ms:.4f}ms exceeded 1ms budget"


def test_construction_perf_under_budget_delay_injection(perf_budget: PerfBudget) -> None:
    """Red-gate: a virtual 10x delay (10ms per construction) must turn the same assertion red."""
    slow = virtual_delayed_sync(_construct, 0.010)
    sample = perf_budget.best_of(slow, n=5, batch=10)
    assert sample.cpu_ms >= 1.0, f"delay-injected {sample.cpu_ms:.4f}ms should exceed 1ms budget"
