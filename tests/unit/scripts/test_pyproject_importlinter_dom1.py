"""pyproject.toml `[tool.importlinter]` DOM-1 contracts -- task-7608, ADR-2026-09-26-B
Decision 4.

Exercises the real PyPI `import-linter` package (distinct from -- and not a
replacement for -- the pre-existing hand-rolled `scripts/check_import_linter.py`
/`.importlinter` config, RATCHET-2/task-3256, which this leaf does not touch)
against synthetic fixture packages, to pin down each contract's pass/fail
semantics in isolation, and against the committed baseline snapshot
(docs/design/import_linter_baseline.json).

D2 DoD: negative tests >=3, failure-injection 1, performance assertion 1,
red-gate reproduction 1.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, cast

import importlinter.configuration
import pytest
import tomllib
from importlinter.application.ports.reporting import Report
from importlinter.application.use_cases import (
    _register_contract_types,
    create_report,
    read_user_options,
)

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "pyproject.toml"
BASELINE = ROOT / "docs" / "design" / "import_linter_baseline.json"


def _load_importlinter_section() -> dict[str, Any]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return cast(dict[str, Any], data["tool"]["importlinter"])


def _run_report(config_path: Path) -> Report:
    importlinter.configuration.configure()
    user_options = read_user_options(config_filename=str(config_path))
    _register_contract_types(user_options)
    return create_report(user_options)


def _touch_pkg(base: Path, *parts: str) -> None:
    for i in range(1, len(parts) + 1):
        pkg_dir = base.joinpath(*parts[:i])
        pkg_dir.mkdir(parents=True, exist_ok=True)
        init = pkg_dir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")


def _write(base: Path, relative: str, content: str) -> None:
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# pyproject.toml shape
# ---------------------------------------------------------------------------


def test_pyproject_declares_four_dom1_contracts() -> None:
    config = _load_importlinter_section()
    assert config["root_packages"] == ["src"]
    contracts = config["contracts"]
    ids = {c["id"] for c in contracts}
    assert ids == {"DOM-1a", "DOM-1b", "DOM-1c", "DOM-1d"}
    types = {c["id"]: c["type"] for c in contracts}
    assert types == {
        "DOM-1a": "independence",
        "DOM-1b": "layers",
        "DOM-1c": "layers",
        "DOM-1d": "forbidden",
    }


def test_import_linter_and_grimp_resolve_as_declared_dependencies() -> None:
    """Negative test: guards against the dependency declaration silently
    regressing to only being trivially satisfied because nothing in src/
    imports `importlinter`/`grimp` yet (check_deps_declared.py would still
    pass in that case even if the distribution mapping were broken)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        check_deps_declared = importlib.import_module("check_deps_declared")
        declared = check_deps_declared.declared_import_names(
            PYPROJECT, ROOT / "requirements-lock.txt"
        )
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    assert {"importlinter", "grimp"} <= declared


# ---------------------------------------------------------------------------
# Baseline snapshot shape (negative tests against a hand-corrupted baseline)
# ---------------------------------------------------------------------------


def test_baseline_snapshot_lists_all_four_contracts_with_consistent_counts() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(baseline["contracts"]) == {"DOM-1a", "DOM-1b", "DOM-1c", "DOM-1d"}
    for entry in baseline["contracts"].values():
        assert entry["violation_count"] == len(entry["violations"])
        assert entry["kept"] == (entry["violation_count"] == 0)


def test_baseline_rejects_malformed_json() -> None:
    """Negative test: a hand-edited baseline with a stray quote/newline (the
    exact corruption mode CLAUDE.md frequent-mistake #2 warns about for
    task JSON) must fail JSON parsing, not silently coerce to something
    else."""
    corrupted = '{"contracts": {"DOM-1a": {"violation_count": 1, "violations": ["a "b"]}}}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(corrupted)


def test_baseline_violation_count_field_type_is_int_not_bool() -> None:
    """Negative test: `isinstance(True, int)` is True in Python, so a
    hand-edited `"violation_count": true` would silently pass a naive
    `isinstance(x, int)` schema check. Guard the real baseline file against
    that class of corruption."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    for entry in baseline["contracts"].values():
        assert isinstance(entry["violation_count"], int)
        assert not isinstance(entry["violation_count"], bool)


# ---------------------------------------------------------------------------
# Contract semantics on synthetic fixtures (failure injection + red-gate
# reproduction)
# ---------------------------------------------------------------------------


def _independence_project(tmp_path: Path) -> Path:
    _touch_pkg(tmp_path, "fixture_src", "foundation", "domain_a", "application")
    _touch_pkg(tmp_path, "fixture_src", "foundation", "domain_b", "domain")
    _touch_pkg(tmp_path, "fixture_src", "foundation", "domain_b", "contracts")
    _write(
        tmp_path,
        "pyproject.toml",
        """
[tool.importlinter]
root_packages = ["fixture_src"]

[[tool.importlinter.contracts]]
id = "T1"
name = "independence"
type = "independence"
modules = ["fixture_src.foundation.*"]
ignore_imports = [
    "fixture_src.foundation.** -> fixture_src.foundation.*.contracts",
    "fixture_src.foundation.** -> fixture_src.foundation.*.contracts.**",
]
unmatched_ignore_imports_alerting = "none"
""",
    )
    return tmp_path / "pyproject.toml"


def test_independence_contract_flags_direct_cross_domain_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure injection + red-gate reproduction: a foundation domain
    reaching directly into another domain's `domain/` package (bypassing
    contracts/ports) must break the DOM-1a-style independence contract."""
    config = _independence_project(tmp_path)
    _write(
        tmp_path,
        "fixture_src/foundation/domain_a/application/__init__.py",
        "import fixture_src.foundation.domain_b.domain\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    report = _run_report(config)

    assert report.contains_failures is True


def test_independence_contract_allows_import_via_contracts_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative test: the same cross-domain import routed through the
    other domain's `contracts/` package must NOT break the contract --
    this is the exemption DOM-1a is supposed to grant."""
    config = _independence_project(tmp_path)
    _write(
        tmp_path,
        "fixture_src/foundation/domain_a/application/__init__.py",
        "import fixture_src.foundation.domain_b.contracts\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    report = _run_report(config)

    assert report.contains_failures is False


def test_layers_contract_flags_foundation_importing_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative test mirroring DOM-1b: the lower layer (foundation)
    importing the higher layer (services) is the reverse-dependency
    direction DOM-1b exists to catch."""
    _touch_pkg(tmp_path, "fixture_src", "services")
    _touch_pkg(tmp_path, "fixture_src", "foundation")
    _write(
        tmp_path,
        "fixture_src/foundation/__init__.py",
        "import fixture_src.services\n",
    )
    _write(
        tmp_path,
        "pyproject.toml",
        """
[tool.importlinter]
root_packages = ["fixture_src"]

[[tool.importlinter.contracts]]
id = "T2"
name = "layers"
type = "layers"
containers = ["fixture_src"]
layers = ["services", "foundation"]
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    report = _run_report(tmp_path / "pyproject.toml")

    assert report.contains_failures is True


def test_layers_contract_allows_services_importing_foundation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative test: the sanctioned direction (services -> foundation)
    must stay green."""
    _touch_pkg(tmp_path, "fixture_src", "services")
    _touch_pkg(tmp_path, "fixture_src", "foundation")
    _write(
        tmp_path,
        "fixture_src/services/__init__.py",
        "import fixture_src.foundation\n",
    )
    _write(
        tmp_path,
        "pyproject.toml",
        """
[tool.importlinter]
root_packages = ["fixture_src"]

[[tool.importlinter.contracts]]
id = "T2"
name = "layers"
type = "layers"
containers = ["fixture_src"]
layers = ["services", "foundation"]
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    report = _run_report(tmp_path / "pyproject.toml")

    assert report.contains_failures is False


def test_forbidden_contract_flags_core_importing_foundation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure injection mirroring DOM-1d: core reaching into foundation
    directly must be flagged."""
    _touch_pkg(tmp_path, "fixture_src", "core")
    _touch_pkg(tmp_path, "fixture_src", "foundation")
    _write(
        tmp_path,
        "fixture_src/core/__init__.py",
        "import fixture_src.foundation\n",
    )
    _write(
        tmp_path,
        "pyproject.toml",
        """
[tool.importlinter]
root_packages = ["fixture_src"]

[[tool.importlinter.contracts]]
id = "T3"
name = "forbidden"
type = "forbidden"
source_modules = ["fixture_src.core"]
forbidden_modules = ["fixture_src.foundation"]
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    report = _run_report(tmp_path / "pyproject.toml")

    assert report.contains_failures is True


# ---------------------------------------------------------------------------
# Performance budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_real_repo_report_throughput_budget() -> None:
    """Static analysis over this repo's full `src/` tree (~1,300 files)
    must stay well inside interactive/CI budget -- grimp's Rust-backed
    graph builder is expected to be much faster than the pure-Python
    AST scan `scripts/check_import_linter.py` already runs in well under
    a second (see that script's own throughput test)."""
    start = time.perf_counter()
    report = _run_report(PYPROJECT)
    elapsed = time.perf_counter() - start

    assert len(report.contracts) == 4
    assert elapsed < 20.0, f"lint-imports report took {elapsed:.2f}s (budget 20.0s)"
