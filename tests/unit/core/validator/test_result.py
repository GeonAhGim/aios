"""Coverage for src/core/validator/result.py (ValidationResult)."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from src.core.validator.result import ValidationResult


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
def test_construction_perf_p95_under_budget() -> None:
    """Simple pydantic model construction must stay well under the 1ms p95 budget."""
    samples = []
    for _ in range(200):
        start = time.perf_counter()
        ValidationResult(is_valid=True, errors=["x"])
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 < 0.001, f"p95 construction time {p95:.6f}s exceeded 1ms budget"
