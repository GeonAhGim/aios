"""Coverage for src/core/validator/result.py (ValidationResult)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.validator.result import ValidationResult
from tests.conftest import PerfBudget


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
    """Simple pydantic model construction must stay well under the 1ms p95 budget."""

    def construct() -> None:
        ValidationResult(is_valid=True, errors=["x"])

    samples = perf_budget.samples(construct, n=100, batch=10)
    cpu_ms_values = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.95) - 1]
    assert p95_ms < 1.0, f"p95 construction time {p95_ms:.4f}ms exceeded 1ms budget"


def test_construction_perf_p95_under_budget_delay_injection(perf_budget: PerfBudget) -> None:
    """Red-gate: inject 200 μs sleep per construction → p95 should turn red against 1ms budget."""

    original_process_time = time.process_time
    call_count = 0

    def slow_process_time() -> float:
        nonlocal call_count
        call_count += 1
        # cumulative offset: each process_time() call adds 0.0002*N seconds.
        # Inside sample(), start is call N and end is call N+1, so the delta
        # gains 0.0002 s = 200 μs. With batch=10, cpu_ms = 200 μs * 1000 / 10 = 20 ms.
        return original_process_time() + 0.01 * call_count

    def construct_with_delay() -> None:
        ValidationResult(is_valid=True, errors=["x"])

    with patch.object(time, "process_time", side_effect=slow_process_time):
        samples = perf_budget.samples(construct_with_delay, n=100, batch=10)
        cpu_ms_values = sorted(s.cpu_ms for s in samples)
        p95_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.95) - 1]
        assert p95_ms >= 1.0, f"delay-injected p95={p95_ms:.4f}ms should exceed 1ms budget"
