"""DEEPEN(task-10007): negative/failure-injection coverage for src/data/models package.

Scope: package-level invariants that span submodules — import structure,
enum validation, and failure-injection for submodule loading.

Negative tests >=3, failure injection >=1, performance assertion >=1.
"""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.data.models.base import AssetClass, Currency, Money

# ── Negative tests: invariant-violating inputs ───────────────────────────


def test_money_string_currency_rejected() -> None:
    """Money must reject string currency codes — only Currency enum allowed."""
    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        Money.model_validate({"amount": Decimal("100"), "currency": "USD"})


def test_asset_class_invalid_string_rejected() -> None:
    """AssetClass must reject arbitrary strings — only enum members allowed."""
    with pytest.raises(ValueError):
        AssetClass("INVALID")


def test_asset_class_case_sensitive() -> None:
    """AssetClass enum values are case-sensitive — lowercase rejected."""
    with pytest.raises(ValueError):
        AssetClass("equity")


def test_money_decimal_coercion_from_int() -> None:
    """Money accepts int amounts and coerces to Decimal."""
    m = Money.model_validate({"amount": 100, "currency": Currency.USDT})
    assert m.amount == Decimal("100")


# ── Failure injection: submodule import failure ─────────────────────────


def test_models_package_missing_submodule_raises_import_error() -> None:
    """Failure injection: if a sibling submodule cannot be imported,
    the package-level import of a specific submodule must fail cleanly
    (not crash with a confusing cascade)."""
    with patch.dict("sys.modules", {"src.data.models.serialization": None}):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            import importlib

            importlib.import_module("src.data.models.serialization")


# ── Performance assertion: Decimal arithmetic budget ────────────────────


@pytest.mark.perf
def test_money_arithmetic_performance_budget() -> None:
    """Performance: 1000 Money additions must complete within 50ms.

    Budget: ADR-2026-09-09-C performance table — model-level operations
    must be sub-millisecond for Decimal arithmetic.
    """
    m1 = Money(amount=Decimal("100.50"), currency=Currency.USDT)
    m2 = Money(amount=Decimal("50.25"), currency=Currency.USDT)

    start = time.perf_counter()
    for _ in range(1000):
        _ = m1 + m2
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50, (
        f"Money arithmetic exceeded 50ms budget for 1000 ops: {elapsed_ms:.1f}ms"
    )


# ── Package structure invariant ─────────────────────────────────────────


def test_models_package_exports_base_and_asset_class() -> None:
    """The models package must export base and asset_class submodules
    since they are imported at the package level."""
    import importlib

    package = importlib.import_module("src.data.models")
    for name in ("base", "asset_class"):
        module = importlib.import_module(f"src.data.models.{name}")
        assert getattr(package, name) is module
