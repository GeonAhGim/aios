"""Tests for the foundation unit-test package boundary.

The package initializer is intentionally small, but it is also the common
import boundary for every foundation unit test.  These checks keep discovery
fail-closed when a caller supplies an invalid package name or when import
metadata cannot be read.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import time
from statistics import quantiles
from typing import Any

import pytest

_PACKAGE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


class _PackageDiscoveryError(RuntimeError):
    """Raised when the unit-test package cannot be discovered safely."""


def _discover_package(package_name: Any) -> importlib.machinery.ModuleSpec:
    """Return a package spec, rejecting malformed or non-package input."""

    if not isinstance(package_name, str) or not _PACKAGE_NAME.fullmatch(package_name):
        raise ValueError("package_name must be a dotted Python package name")

    try:
        spec = importlib.util.find_spec(package_name)
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        raise _PackageDiscoveryError("package discovery failed") from exc
    if spec is None or spec.submodule_search_locations is None:
        raise _PackageDiscoveryError("package is not importable as a package")
    return spec


def test_unit_package_is_discoverable() -> None:
    """The foundation unit-test package must remain an importable package."""

    spec = _discover_package(__package__)

    assert spec.name == "tests.foundation.unit"
    assert spec.submodule_search_locations


@pytest.mark.parametrize("invalid_name", [None, "", "tests..foundation"])
def test_discovery_rejects_invalid_package_names(invalid_name: Any) -> None:
    """Malformed package names are rejected before importlib is called."""

    with pytest.raises(ValueError, match="dotted Python package name"):
        _discover_package(invalid_name)


def test_discovery_rejects_a_module_spec_without_package_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A module must not be accepted as a package discovery result."""

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda _name: importlib.machinery.ModuleSpec("module", loader=None),
    )

    with pytest.raises(_PackageDiscoveryError, match="not importable as a package"):
        _discover_package("tests.foundation.unit")


def test_discovery_fails_closed_when_importlib_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dependency failures must be translated to the package error."""

    def raise_import_error(_name: str) -> None:
        raise ImportError("simulated metadata outage")

    monkeypatch.setattr(importlib.util, "find_spec", raise_import_error)

    with pytest.raises(_PackageDiscoveryError, match="discovery failed"):
        _discover_package("tests.foundation.unit")


def test_package_discovery_p95_is_within_budget() -> None:
    """Repeated package discovery remains below the 50 ms p95 budget."""

    samples: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        _discover_package("tests.foundation.unit")
        samples.append((time.perf_counter() - started) * 1000)

    p95 = quantiles(samples, n=20, method="inclusive")[18]
    assert p95 < 50.0
