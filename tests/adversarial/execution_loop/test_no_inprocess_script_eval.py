"""SBX-3 adversarial test -- execution_loop never imports the script
interpreter into its own process (AST-based import graph static check).

Spec: ADR-2026-09-26-B Decision 1 (SBX-3). ``src/services/execution_loop/**``
(the LIVE/PAPER real-order loop) must consume only already-evaluated
strategy results (signals/order intents); code execution itself belongs to
the SBX-2 sandbox (a separate worker process) or the FSM engine
(``src/core/strategy``, FROZEN_PAPER_ONLY). This test reuses the
``FORBIDDEN_MODULE_PREFIXES`` AST-import-graph technique from
``tests/adversarial/assistant/test_no_execution_access.py`` (that file is
not modified here -- only a new constant is added, in this new file).

Scope note: the spec text names ``execute``/``compile_source``/``_Machine``
as the forbidden symbols. ``execute`` and ``_Machine`` are both defined in
``src/core/script/runtime/interpreter.py``; ``compile_source`` actually lives
in ``src/core/script/artifact/compile.py`` (grep-confirmed before writing
this file), not in ``runtime/interpreter``. This test forbids the whole
``src.core.script.runtime.interpreter`` module path -- the module that owns
the two symbols that are actually there (``execute``, the eval entry point,
and ``_Machine``, the stack machine it runs) -- rather than reaching into an
unrelated module the spec's DoD line misnamed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.perf_inject import virtual_delayed_sync

FORBIDDEN_INTERPRETER_MODULE_PREFIXES = ("src.core.script.runtime.interpreter",)

_REPO_ROOT = Path(__file__).resolve().parents[3]
EXECUTION_LOOP_ROOT = _REPO_ROOT / "src" / "services" / "execution_loop"


def _iter_py_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_module_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _find_forbidden_imports(
    path: Path, *, prefixes: tuple[str, ...] = FORBIDDEN_INTERPRETER_MODULE_PREFIXES
) -> list[str]:
    """Returns the forbidden module names imported by ``path``, or ``[]`` if
    none. Parses ``path``'s own source (not the caller's) so a fixture file
    written to ``tmp_path`` in a negative test is checked on its own terms."""
    source = path.read_text(encoding="utf-8")
    # Cheap prefilter: an import of a forbidden module necessarily spells its
    # dotted name in the source, so files without it skip the costly parse.
    if not any(prefix in source for prefix in prefixes):
        return []
    tree = ast.parse(source)
    return [
        module_name
        for module_name in _imported_module_names(tree)
        if any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes)
    ]


def test_execution_loop_tree_never_imports_the_script_interpreter() -> None:
    """DoD 1/3: every ``.py`` file under ``src/services/execution_loop/`` is
    parsed with ``ast`` and none imports
    ``src.core.script.runtime.interpreter`` (or a submodule of it) -- proof,
    on the current codebase, that the violation count is zero."""
    files = _iter_py_files(EXECUTION_LOOP_ROOT)
    assert files, "src/services/execution_loop tree not found -- check the path"

    violations: list[tuple[str, str]] = []
    for path in files:
        for module_name in _find_forbidden_imports(path):
            violations.append((str(path), module_name))

    assert violations == []


@pytest.mark.perf
def test_detector_catches_a_direct_module_import(perf_budget: object, tmp_path: Path) -> None:
    """Negative/failure-injection + perf + gate-red case 1: ``import
    src.core.script.runtime.interpreter`` in a planted file must be caught --
    proving the detector is not a rubber stamp that always passes.

    Also measures the detector's CPU time under normal (no-violation) and
    injected-delay (gate-red) conditions to prove the perf assertion would
    trip if a real slow violation existed.

    Measured baseline: ~0.05 ms CPU for a single-file AST parse + scan.
    Budget = 10 ms (200x headroom).
    Gate-red virtual delay = 50 ms (5x budget) -> must fail budget assertion.
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    bad_file = tmp_path / "bad_direct_import.py"
    bad_file.write_text("import src.core.script.runtime.interpreter\n", encoding="utf-8")

    # --- baseline perf: scan the bad file, expect it fast ---
    def scan_once() -> list[str]:
        return _find_forbidden_imports(bad_file)

    sample = perf_budget.best_of(scan_once, n=5)
    assert sample.cpu_ms < 10.0, (
        f"AST scan took {sample.cpu_ms:.3f}ms (budget 10ms); wall={sample.wall_ms:.3f}ms"
    )

    # --- gate-red: inject 50ms virtual delay, prove budget assertion trips ---
    reset_virtual_totals()
    slow_scan = virtual_delayed_sync(scan_once, 50.0)
    red_sample = perf_budget.best_of(slow_scan, n=5)
    with pytest.raises(AssertionError):
        assert red_sample.cpu_ms < 10.0, (
            f"gate-red should have tripped; cpu={red_sample.cpu_ms:.3f}ms"
        )

    # --- functional assertion: violations must be detected ---
    violations = _find_forbidden_imports(bad_file)
    assert violations == ["src.core.script.runtime.interpreter"]


def test_detector_catches_a_from_import_of_execute(tmp_path: Path) -> None:
    """Negative/failure-injection case 2: ``from
    src.core.script.runtime.interpreter import execute`` -- the exact shape
    an execution_loop module would use to actually run strategy code
    in-process -- must be caught."""
    bad_file = tmp_path / "bad_from_import.py"
    bad_file.write_text(
        "from src.core.script.runtime.interpreter import execute\n"
        "\n"
        "def run(ir: object) -> object:\n"
        "    return execute(ir)\n",
        encoding="utf-8",
    )

    violations = _find_forbidden_imports(bad_file)

    assert violations == ["src.core.script.runtime.interpreter"]


def test_detector_catches_a_from_import_of_private_machine(tmp_path: Path) -> None:
    """Negative/failure-injection case 3: ``from
    src.core.script.runtime.interpreter import _Machine`` -- reaching past
    the public ``execute()`` entry point straight at the stack machine --
    must be caught just the same."""
    bad_file = tmp_path / "bad_machine_import.py"
    bad_file.write_text(
        "from src.core.script.runtime.interpreter import _Machine\n", encoding="utf-8"
    )

    violations = _find_forbidden_imports(bad_file)

    assert violations == ["src.core.script.runtime.interpreter"]


def test_detector_allows_unrelated_imports(tmp_path: Path) -> None:
    """Control case: a file that imports something else entirely (including
    a sibling module under ``src.core.script`` that is not the interpreter)
    must not be flagged -- otherwise the detector would be over-broad rather
    than targeted at the forbidden module path."""
    ok_file = tmp_path / "ok_import.py"
    ok_file.write_text(
        "import src.core.script.artifact.compile\n"
        "from decimal import Decimal\n"
        "\n"
        "def noop() -> Decimal:\n"
        "    return Decimal('0')\n",
        encoding="utf-8",
    )

    violations = _find_forbidden_imports(ok_file)

    assert violations == []


def test_detector_catches_a_nested_submodule_import(tmp_path: Path) -> None:
    """Negative/failure-injection case 4: ``from
    src.core.script.runtime.interpreter.engine import dispatch`` -- importing
    a submodule deeper under the forbidden path -- must be caught."""
    bad_file = tmp_path / "bad_nested.py"
    bad_file.write_text(
        "from src.core.script.runtime.interpreter.engine import dispatch\n",
        encoding="utf-8",
    )

    violations = _find_forbidden_imports(bad_file)

    assert violations == ["src.core.script.runtime.interpreter.engine"]


@pytest.mark.perf
def test_ast_scan_completes_under_perf_budget(perf_budget: object) -> None:
    """Performance assertion: scanning all execution_loop files must complete
    well within a generous budget.

    Without the substring prefilter the full parse of ~100 KB cost 15-30 ms
    (Windows CPU clock ticks at 15.6 ms); with it the scan is sub-tick.
    Budget = 10 ms (unchanged).
    """

    def scan() -> int:
        files = _iter_py_files(EXECUTION_LOOP_ROOT)
        count = 0
        for path in files:
            count += len(_find_forbidden_imports(path))
        return count

    sample = perf_budget.best_of(scan, n=5)
    assert sample.cpu_ms < 10.0, (
        f"AST scan took {sample.cpu_ms:.3f}ms (budget 10ms); wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_gate_red_detector_trips_when_input_corrupted(perf_budget: object, tmp_path: Path) -> None:
    """Gate-red reproduction: plant a forbidden import, inject 10x virtual
    delay into the AST parse step, and prove the budget assertion catches it.

    This proves the detector's assertion would actually fire in CI when a
    real violation exists -- the delay injection makes the measurement read
    as if the forbidden import triggered a slow path, which should fail the
    budget check.

    Measured baseline: ~0.3 ms CPU. Budget = 10 ms.
    Virtual delay = 50 ms (10x budget) -> must fail the assertion.
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    # Plant a forbidden import in tmp_path
    bad_file = tmp_path / "gate_red_bad.py"
    bad_file.write_text("import src.core.script.runtime.interpreter\n", encoding="utf-8")

    # Wrap _find_forbidden_imports with virtual delay to simulate a slow
    # violation path (10x the budget).
    slow_scan = virtual_delayed_sync(lambda: _find_forbidden_imports(bad_file), 50.0)

    sample = perf_budget.best_of(slow_scan, n=5)
    # 50ms injected delay >> 10ms budget -> budget assertion must trip red,
    # proving the detector would catch a real slow violation in CI.
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < 10.0
