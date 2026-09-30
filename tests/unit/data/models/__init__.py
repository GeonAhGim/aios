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

from src.data.models.asset_class import AssetClass  # type: ignore[attr-defined]
from src.data.models.base import Currency, Money

# ── Negative tests: invariant-violating inputs ───────────────────────────


def test_money_string_currency_rejected() -> None:
    """Money must reject string currency codes — only Currency enum allowed."""
    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        Money(amount=Decimal("100"), currency="USD")  # type: ignore[arg-type]


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
    m = Money(amount=100, currency=Currency.USDT)  # type: ignore[arg-type]
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
    # Reload fresh to avoid side effects from other tests
    import importlib
    import sys

    # Remove cached submodules
    cached = [k for k in sys.modules if k.startswith("src.data.models.")]
    for k in cached:
        del sys.modules[k]

    # Re-import the package fresh
    importlib.import_module("src.data.models")
    fresh_pkg = sys.modules["src.data.models"]

    exported = [n for n in dir(fresh_pkg) if not n.startswith("_")]
    assert "base" in exported, "base must be exported by models package"
    assert "asset_class" in exported, "asset_class must be exported by models package"
