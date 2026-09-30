"""Helpers for asserting that frozen models and dataclasses reject mutation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


def assign_attr(target: Any, name: str, value: object) -> None:
    """Assign `target.<name> = value` through the normal attribute protocol.

    Frozen dataclasses and frozen pydantic models raise on assignment. Routing the
    write through `setattr` with a runtime attribute name keeps the call site
    type-clean (a literal `obj.field = ...` on a frozen type is a static error)
    while exercising exactly the same rejection path.
    """
    setattr(target, name, value)


# ---------------------------------------------------------------------------
# Tests for assign_attr — negative / failure-injection / performance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenPoint:
    x: int
    y: int


@dataclass(frozen=True)
class FrozenConfig:
    timeout: int
    retries: int


class TestAssignAttrNegative:
    """Negative tests: assign_attr must raise AttributeError on frozen types."""

    def test_rejects_existing_field_on_frozen_dataclass(self) -> None:
        """Mutating an existing field on a frozen dataclass raises."""
        obj = FrozenPoint(x=1, y=2)
        with pytest.raises(AttributeError):
            assign_attr(obj, "x", 999)

    def test_rejects_new_field_on_frozen_dataclass(self) -> None:
        """Adding a new field on a frozen dataclass raises."""
        obj = FrozenPoint(x=1, y=2)
        with pytest.raises(AttributeError):
            assign_attr(obj, "z", 3)

    def test_rejects_multiple_field_on_frozen_dataclass(self) -> None:
        """Mutating a non-primary field on a frozen dataclass raises."""
        obj = FrozenConfig(timeout=30, retries=3)
        with pytest.raises(AttributeError):
            assign_attr(obj, "retries", 10)

    def test_rejects_on_non_frozen_dataclass_with_frozen_true(self) -> None:
        """Frozen=True dataclass rejects even with complex nested values."""
        obj = FrozenConfig(timeout=60, retries=5)
        with pytest.raises(AttributeError):
            assign_attr(obj, "timeout", float("inf"))


class TestAssignAttrPositive:
    """Positive tests: assign_attr must succeed on mutable objects."""

    def test_allows_mutation_on_plain_object(self) -> None:
        """setattr on a regular object succeeds."""
        obj = type("Mutable", (), {})()
        assign_attr(obj, "field", "value")
        assert obj.field == "value"

    def test_allows_mutation_on_dict_as_object(self) -> None:
        """setattr on a plain object with __dict__ succeeds."""

        class Mutable:
            pass

        obj: Any = Mutable()
        assign_attr(obj, "key", 42)
        assert obj.key == 42


class TestAssignAttrFailureInjection:
    """Failure injection: setattr itself can raise non-AttributeError."""

    def test_propagates_runtime_error_from_custom_setattr(self) -> None:
        """If the target's __setattr__ raises RuntimeError, it propagates."""
        obj = type(
            "Broken",
            (),
            {"__setattr__": lambda s, n, v: (_ for _ in ()).throw(RuntimeError("broken"))},
        )()
        with pytest.raises(RuntimeError, match="broken"):
            assign_attr(obj, "field", "value")

    def test_propagates_type_error_from_setattr(self) -> None:
        """TypeError from setattr is not swallowed."""
        obj = type(
            "Strict",
            (),
            {"__setattr__": lambda s, n, v: (_ for _ in ()).throw(TypeError("bad type"))},
        )()
        with pytest.raises(TypeError, match="bad type"):
            assign_attr(obj, "field", "value")

    def test_builtin_setattr_monkeypatch_raises(self) -> None:
        """Patching builtins.setattr raises OSError through assign_attr."""
        import builtins

        def _fake_setattr(target: Any, name: str, value: object) -> None:
            raise OSError("simulated I/O failure")

        obj = FrozenPoint(x=1, y=2)
        # Temporarily replace builtins.setattr at the C level via the module dict
        old_setattr = builtins.setattr
        try:
            builtins.setattr = _fake_setattr
            with pytest.raises(OSError, match="simulated I/O failure"):
                assign_attr(obj, "x", 999)
        finally:
            builtins.setattr = old_setattr


class TestAssignAttrPerformance:
    """Performance assertion: assign_attr must complete within budget."""

    @pytest.mark.perf
    def test_assign_attr_within_budget(self) -> None:
        """assign_attr(10000 calls) must complete in < 100ms (budget: 10µs/call)."""
        import time

        obj = type("Mutable", (), {})()
        iterations = 10_000
        start = time.perf_counter()
        for i in range(iterations):
            assign_attr(obj, f"field_{i}", i)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, (
            f"assign_attr performance budget exceeded: "
            f"{elapsed_ms:.1f}ms for {iterations} calls "
            f"(> 10µs/call)"
        )
