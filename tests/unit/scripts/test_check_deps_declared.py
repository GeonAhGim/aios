"""scripts/check_deps_declared.py unit tests -- task-7508.

Covers the AST import scan, the pyproject.toml/requirements-lock.txt ->
importable-module-name resolution (installed-package metadata, explicit
alias table, normalized fallback), the TYPE_CHECKING/optional-except
carve-outs, and the CLI's exit-code contract. D2 DoD: negative tests >= 3,
one failure-injection test (an undeclared import -> rc=1, reproducing the
gate going red), one numeric performance assertion.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._perf.relative_budget import RelativeBudget

# task-7741(esc-ci-coverage): fixed 5s wall-clock budget for a 300-file disk
# scan is the same false-red shape as scripts/coverage_ratchet.py documents
# for the local CI coverage step (pytest --cov=src line-tracer overhead +
# shared-host contention). Switched to RelativeBudget (task-7631 pattern) --
# a ratio against a same-process calibration loop instead of an absolute
# second figure, so it self-corrects for host speed/load.
_SCAN_IMPORTS_MAX_RATIO = 60.0

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cdd = _load_module("check_deps_declared", SCRIPTS_DIR / "check_deps_declared.py")


def _write(base: Path, relative: str, content: str) -> Path:
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_pyproject(
    base: Path, dependencies: list[str], optional: dict[str, list[str]] | None = None
) -> Path:
    lines = ["[project]", 'name = "fixture"', "dependencies = ["]
    lines += [f'    "{dep}",' for dep in dependencies]
    lines.append("]")
    if optional:
        lines.append("[project.optional-dependencies]")
        for group, deps in optional.items():
            lines.append(f"{group} = [")
            lines += [f'    "{dep}",' for dep in deps]
            lines.append("]")
    return _write(base, "pyproject.toml", "\n".join(lines) + "\n")


def _write_lockfile(base: Path, pins: list[str]) -> Path:
    return _write(base, "requirements-lock.txt", "\n".join(pins) + "\n")


# ---------------------------------------------------------------------------
# _dependency_name / _normalize
# ---------------------------------------------------------------------------


def test_dependency_name_strips_extras_and_version_and_marker() -> None:
    assert cdd._dependency_name("uvicorn[standard]>=0.32") == "uvicorn"
    assert cdd._dependency_name("tzdata>=2024.1; sys_platform == 'win32'") == "tzdata"
    assert cdd._dependency_name("TA-Lib==0.7.1") == "TA-Lib"


def test_normalize_lowercases_and_replaces_hyphen() -> None:
    assert cdd._normalize("PyYAML") == "pyyaml"
    assert cdd._normalize("python-dotenv") == "python_dotenv"


# ---------------------------------------------------------------------------
# resolve_import_names -- installed-metadata path, alias path, fallback path
# ---------------------------------------------------------------------------


def test_resolve_import_names_uses_installed_metadata_when_available() -> None:
    names = cdd.resolve_import_names(["pyyaml>=6.0"], installed_map={"pyyaml": {"yaml", "_yaml"}})
    assert names == {"yaml", "_yaml"}


def test_resolve_import_names_alias_table_used_when_not_installed() -> None:
    """Negative test: alias mapping works even when the distribution is not
    present in the running interpreter (module docstring point 2)."""
    names = cdd.resolve_import_names(
        ["PyYAML>=6.0", "pyjwt>=2.9", "argon2-cffi>=23.1", "python-dotenv>=1.0", "TA-Lib==0.7.1"],
        installed_map={},
    )
    assert names == {"yaml", "jwt", "argon2", "dotenv", "talib"}


def test_resolve_import_names_falls_back_to_normalized_name() -> None:
    names = cdd.resolve_import_names(["some-unknown-package>=1.0"], installed_map={})
    assert names == {"some_unknown_package"}


# ---------------------------------------------------------------------------
# scan_imports -- stdlib exclusion, in-repo exclusion, relative imports,
# TYPE_CHECKING / try-except-ImportError carve-outs
# ---------------------------------------------------------------------------


def test_scan_imports_and_find_undeclared_excludes_stdlib(tmp_path: Path) -> None:
    """Negative test: importing only stdlib modules produces no undeclared
    imports -- scan_imports records them, find_undeclared filters them out
    via sys.stdlib_module_names."""
    _write(tmp_path, "src/mod.py", "import os\nimport json\nfrom collections import OrderedDict\n")
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert failures == []
    assert warnings == []


def test_scan_imports_excludes_in_repo_packages(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/mod.py", "import src.pkg.other\nimport tests.helpers\n")
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert failures == []
    assert warnings == []


def test_scan_imports_skips_relative_imports(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/mod.py", "from . import other\nfrom .sibling import thing\n")
    hits = cdd.scan_imports(tmp_path)
    assert hits == []


def test_undeclared_import_detected(tmp_path: Path) -> None:
    """Negative test: a genuinely undeclared third-party import is flagged
    with file:line and module name."""
    _write(tmp_path, "src/pkg/mod.py", "import definitely_undeclared_pkg\n")
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert warnings == []
    assert len(failures) == 1
    assert failures[0].module == "definitely_undeclared_pkg"
    assert failures[0].file == "src/pkg/mod.py"
    assert failures[0].line == 1


def test_type_checking_import_is_warning_not_failure(tmp_path: Path) -> None:
    """Negative test: an undeclared import used only for type hints (guarded
    by `if TYPE_CHECKING:`) is reported as a warning, never a hard failure."""
    _write(
        tmp_path,
        "src/pkg/mod.py",
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import some_typing_only_pkg\n",
    )
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert failures == []
    assert len(warnings) == 1
    assert warnings[0].module == "some_typing_only_pkg"


def test_try_except_import_error_is_warning_not_failure(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/pkg/mod.py",
        "try:\n    import optional_pkg\nexcept ImportError:\n    optional_pkg = None\n",
    )
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert failures == []
    assert len(warnings) == 1
    assert warnings[0].module == "optional_pkg"


def test_try_except_module_not_found_error_is_also_a_warning(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/pkg/mod.py",
        "try:\n    import optional_pkg\nexcept (ImportError, ModuleNotFoundError):\n    pass\n",
    )
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert failures == []
    assert len(warnings) == 1


def test_try_except_other_error_is_still_a_failure(tmp_path: Path) -> None:
    """An undeclared import guarded by an unrelated except clause (not
    ImportError) is not an optional-dependency pattern -- still a failure."""
    _write(
        tmp_path,
        "src/pkg/mod.py",
        "try:\n    import undeclared_pkg\nexcept ValueError:\n    pass\n",
    )
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared=set())
    assert warnings == []
    assert len(failures) == 1


def test_declared_import_is_neither_failure_nor_warning(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/mod.py", "import redis\n")
    hits = cdd.scan_imports(tmp_path)
    failures, warnings = cdd.find_undeclared(hits, declared={"redis"})
    assert failures == []
    assert warnings == []


# ---------------------------------------------------------------------------
# declared_import_names -- pyproject.toml + requirements-lock.txt combined
# ---------------------------------------------------------------------------


def test_declared_import_names_reads_pyproject_and_lockfile(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, dependencies=["redis>=5,<9"], optional={"dev": ["ruff>=0.16"]})
    _write_lockfile(tmp_path, ["redis==5.0.0", "ruff==0.16.0", "starlette==1.6.0"])
    names = cdd.declared_import_names(
        tmp_path / "pyproject.toml", tmp_path / "requirements-lock.txt"
    )
    assert {"redis", "ruff", "starlette"} <= names


def test_declared_import_names_missing_pyproject_raises() -> None:
    """Failure injection: a missing pyproject.toml raises DepsDeclaredError
    (fail-closed) instead of crashing with a bare traceback."""
    with pytest.raises(cdd.DepsDeclaredError):
        cdd.declared_import_names(Path("/nonexistent/pyproject.toml"), Path("/nonexistent/req.txt"))


# ---------------------------------------------------------------------------
# main() -- CLI exit-code contract, red-gate reproduction
# ---------------------------------------------------------------------------


def _scenario_with_undeclared_import(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, dependencies=["redis>=5,<9"])
    _write_lockfile(tmp_path, ["redis==5.0.0"])
    _write(tmp_path, "src/pkg/mod.py", "import redis\nimport definitely_undeclared_pkg\n")


def test_main_exits_1_on_undeclared_import(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failure injection / red-gate reproduction: a fake src tree carrying one
    undeclared import makes the CLI exit 1 and print file:line and module."""
    _scenario_with_undeclared_import(tmp_path)

    exit_code = cdd.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "definitely_undeclared_pkg" in out
    assert "src/pkg/mod.py:2" in out


def test_main_exits_0_when_all_imports_declared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_pyproject(tmp_path, dependencies=["redis>=5,<9"])
    _write_lockfile(tmp_path, ["redis==5.0.0"])
    _write(tmp_path, "src/pkg/mod.py", "import redis\nimport os\n")

    exit_code = cdd.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "OK" in out


def test_main_json_flag_emits_machine_readable_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _scenario_with_undeclared_import(tmp_path)

    exit_code = cdd.main(["--root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["undeclared"] == [
        {"file": "src/pkg/mod.py", "line": 2, "module": "definitely_undeclared_pkg"}
    ]
    assert payload["warnings"] == []


def test_main_exits_1_on_missing_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failure injection: a repo root with no pyproject.toml at all fails
    closed (exit 1) rather than raising."""
    _write(tmp_path, "src/pkg/mod.py", "import os\n")

    exit_code = cdd.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "pyproject.toml" in out


# ---------------------------------------------------------------------------
# Performance assertion
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_scan_imports_throughput_budget(tmp_path: Path) -> None:
    """300 synthetic modules scan within a self-calibrating budget (D2 DoD
    numeric performance assertion)."""
    for i in range(300):
        _write(
            tmp_path,
            f"src/pkg/mod_{i}.py",
            f"import json\nimport redis\nimport pkg_{i}\n",
        )

    hits: list[object] = []

    def run() -> None:
        hits[:] = cdd.scan_imports(tmp_path)

    RelativeBudget().assert_within(
        run,
        max_ratio=_SCAN_IMPORTS_MAX_RATIO,
        mode="wall",
        n=3,
        warmup=1,
        label="scan_imports(300 modules)",
    )

    assert len(hits) == 300 * 3


# ---------------------------------------------------------------------------
# Real-repo smoke check -- current main's own declared-dependency gap
# ---------------------------------------------------------------------------


def test_real_repo_scan_runs_without_crashing() -> None:
    """Smoke test against the actual repo tree: whatever it finds (declared
    or not), the scan must complete without raising."""
    declared = cdd.declared_import_names(ROOT / "pyproject.toml", ROOT / "requirements-lock.txt")
    hits = cdd.scan_imports(ROOT)
    failures, warnings = cdd.find_undeclared(hits, declared)
    assert isinstance(failures, list)
    assert isinstance(warnings, list)
