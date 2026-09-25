"""Local CI "deps_declared" stage (task-7508).

Scans `src/**/*.py` for top-level imported module names and flags any that
are neither part of the standard library nor declared as a dependency of
this project. This is the gate that should have caught `redis`/`lightgbm`
being imported in `src/` before they were added to `pyproject.toml`
(background: those two broke the GitHub Quality Gate before being declared).

Design:

1. AST-scan `src/**/*.py` for the top-level segment of every absolute
   import (`import a.b` / `from a.b import c` -> `a`). Relative imports
   (`from . import x`, `from .foo import y`) are skipped -- they always
   resolve inside the repo. Imports of the in-repo top packages
   (`src`, `tests`, `scripts`) are skipped too. Standard-library modules
   (`sys.stdlib_module_names`) are excluded.
2. The declared set is built from `pyproject.toml`'s `[project.dependencies]`
   and `[project.optional-dependencies]`, plus every pinned name in
   `requirements-lock.txt` (which also carries transitive dependencies that
   are legitimately importable, e.g. `starlette` via `fastapi`). Each
   distribution name is mapped to its importable module name(s) via
   `importlib.metadata.packages_distributions()` when the distribution is
   installed in the running interpreter; otherwise via an explicit alias
   table, falling back to `lower().replace("-", "_")` normalization.
3. An import found only inside an `if TYPE_CHECKING:` block, or inside a
   `try: ... except ImportError:` (or `ModuleNotFoundError`) guard, is
   reported separately as a warning rather than a failure -- both are
   established patterns for an optional/type-only dependency.

Usage: `python scripts/check_deps_declared.py [--json]` (repo root).
Exit code: 0 = no undeclared hard imports, 1 = undeclared import(s) found
(or a required input file is missing/unparseable).
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no stdlib tomllib
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
SCAN_SUBDIR = "src"
DEFAULT_PYPROJECT = ROOT / "pyproject.toml"
DEFAULT_LOCKFILE = ROOT / "requirements-lock.txt"

_EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
    }
)

# In-repo top-level packages: never an external dependency.
_IN_REPO_TOP_PACKAGES = frozenset({"src", "tests", "scripts"})

# Explicit alias table for distributions whose importable module name does not
# match `lower().replace("-", "_")` normalization -- used when the
# distribution is not installed in the running interpreter (see module
# docstring point 2), so a name is still recognized as declared without
# requiring it to be installed here.
_DIST_TO_IMPORT_ALIASES: dict[str, str] = {
    "pyyaml": "yaml",
    "pyjwt": "jwt",
    "pillow": "pil",
    "psycopg": "psycopg",
    "python_dateutil": "dateutil",
    "python_dotenv": "dotenv",
    "argon2_cffi": "argon2",
    "ta_lib": "talib",
    "pytest_xdist": "xdist",
    "beautifulsoup4": "bs4",
    "protobuf": "google",
}

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

Hit = tuple[str, int]  # (rel_path, lineno)


class DepsDeclaredError(ValueError):
    """Input file (pyproject.toml/requirements-lock.txt) missing or malformed."""


@dataclass(frozen=True)
class ImportHit:
    module: str
    file: str
    line: int
    in_type_checking: bool
    in_optional_except: bool


# ---------------------------------------------------------------------------
# Import scan
# ---------------------------------------------------------------------------


def _iter_python_files(root: Path, subdir: str) -> list[Path]:
    base = root / subdir
    if not base.is_dir():
        return []
    out = []
    for path in base.rglob("*.py"):
        if _EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts[:-1]):
            continue
        out.append(path)
    return sorted(out)


def _is_type_checking_test(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _exception_names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for elt in node.elts:
            names |= _exception_names(elt)
        return names
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    return set()


def _handles_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    names = _exception_names(handler.type)
    return "ImportError" in names or "ModuleNotFoundError" in names


class _ImportVisitor(ast.NodeVisitor):
    """Walks a module recording every absolute top-level import, tracking
    whether it sits inside a `TYPE_CHECKING` guard or an optional
    `try/except ImportError` guard."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.hits: list[ImportHit] = []
        self._type_checking_depth = 0
        self._optional_depth = 0

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        optional = any(_handles_import_error(h) for h in node.handlers)
        if optional:
            self._optional_depth += 1
            for child in node.body:
                self.visit(child)
            self._optional_depth -= 1
        else:
            for child in node.body:
                self.visit(child)
        for handler in node.handlers:
            self.visit(handler)
        for child in [*node.orelse, *node.finalbody]:
            self.visit(child)

    def _record(self, top_module: str, lineno: int) -> None:
        self.hits.append(
            ImportHit(
                module=top_module,
                file=self.rel_path,
                line=lineno,
                in_type_checking=self._type_checking_depth > 0,
                in_optional_except=self._optional_depth > 0,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name.split(".")[0], node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            return  # relative import -- always in-repo
        if node.module is None:
            return
        self._record(node.module.split(".")[0], node.lineno)


def scan_imports(root: Path, subdir: str = SCAN_SUBDIR) -> list[ImportHit]:
    hits: list[ImportHit] = []
    for path in _iter_python_files(root, subdir):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except SyntaxError:
            continue
        visitor = _ImportVisitor(rel)
        visitor.visit(tree)
        hits.extend(visitor.hits)
    return hits


# ---------------------------------------------------------------------------
# Declared-dependency set
# ---------------------------------------------------------------------------


def _dependency_name(spec: str) -> str | None:
    """Extracts the bare distribution name from a PEP 508 requirement string
    (`"uvicorn[standard]>=0.32"` -> `"uvicorn"`, `"tzdata>=1; sys_platform ==
    'win32'"` -> `"tzdata"`)."""
    match = _NAME_RE.match(spec)
    return match.group(1) if match else None


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def read_pyproject_dependency_specs(path: Path) -> list[str]:
    if not path.exists():
        raise DepsDeclaredError(f"pyproject.toml not found: {path}")
    with path.open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise DepsDeclaredError(f"failed to parse pyproject.toml: {exc}") from exc
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    for group in optional.values():
        specs.extend(group)
    return specs


def read_lockfile_dependency_specs(path: Path) -> list[str]:
    if not path.exists():
        raise DepsDeclaredError(f"requirements-lock.txt not found: {path}")
    specs: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if line:
            specs.append(line)
    return specs


def _installed_distribution_import_map() -> dict[str, set[str]]:
    """Normalized distribution name -> set of importable module names, built
    from every distribution installed in the running interpreter."""
    mapping: dict[str, set[str]] = {}
    for import_name, dist_names in importlib.metadata.packages_distributions().items():
        for dist_name in dist_names:
            mapping.setdefault(_normalize(dist_name), set()).add(import_name)
    return mapping


def resolve_import_names(
    dependency_specs: list[str], installed_map: dict[str, set[str]] | None = None
) -> set[str]:
    """Maps a list of PEP 508 requirement strings to the set of importable
    top-level module names they provide (see module docstring point 2)."""
    if installed_map is None:
        installed_map = _installed_distribution_import_map()

    names: set[str] = set()
    for spec in dependency_specs:
        dist_name = _dependency_name(spec)
        if dist_name is None:
            continue
        norm = _normalize(dist_name)
        if norm in installed_map:
            names.update(installed_map[norm])
        elif norm in _DIST_TO_IMPORT_ALIASES:
            names.add(_DIST_TO_IMPORT_ALIASES[norm])
        else:
            names.add(norm)
    return names


def declared_import_names(pyproject_path: Path, lockfile_path: Path) -> set[str]:
    specs = [
        *read_pyproject_dependency_specs(pyproject_path),
        *read_lockfile_dependency_specs(lockfile_path),
    ]
    return resolve_import_names(specs)


# ---------------------------------------------------------------------------
# Undeclared-import evaluation
# ---------------------------------------------------------------------------


def _is_stdlib(module: str) -> bool:
    return module in sys.stdlib_module_names


def find_undeclared(
    hits: list[ImportHit], declared: set[str]
) -> tuple[list[ImportHit], list[ImportHit]]:
    """Splits import hits that are neither stdlib nor an in-repo package nor
    declared into (hard failures, warnings) -- an import that only appears
    behind a `TYPE_CHECKING`/optional-`except ImportError` guard is a
    warning, never a failure (module docstring point 3)."""
    failures: list[ImportHit] = []
    warnings: list[ImportHit] = []
    for hit in hits:
        if _is_stdlib(hit.module) or hit.module in _IN_REPO_TOP_PACKAGES:
            continue
        if hit.module in declared:
            continue
        if hit.in_type_checking or hit.in_optional_except:
            warnings.append(hit)
        else:
            failures.append(hit)
    return sorted(set(failures), key=lambda h: (h.file, h.line)), sorted(
        set(warnings), key=lambda h: (h.file, h.line)
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _report_text(failures: list[ImportHit], warnings: list[ImportHit]) -> None:
    if warnings:
        print(f"WARN: {len(warnings)} optional/type-only undeclared import(s):")
        for hit in warnings:
            print(f"  {hit.file}:{hit.line}: {hit.module}")
    if failures:
        print(f"FAIL: {len(failures)} undeclared import(s) in src/:")
        for hit in failures:
            print(f"  {hit.file}:{hit.line}: {hit.module}")
        return
    print("OK: no undeclared imports in src/")


def _report_json(failures: list[ImportHit], warnings: list[ImportHit]) -> None:
    payload = {
        "undeclared": [{"file": h.file, "line": h.line, "module": h.module} for h in failures],
        "warnings": [{"file": h.file, "line": h.line, "module": h.module} for h in warnings],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--pyproject", type=Path, default=None)
    parser.add_argument("--lockfile", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    args = parser.parse_args(argv)

    pyproject_path = args.pyproject or (args.root / "pyproject.toml")
    lockfile_path = args.lockfile or (args.root / "requirements-lock.txt")

    try:
        declared = declared_import_names(pyproject_path, lockfile_path)
    except DepsDeclaredError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_imports(args.root)
    failures, warnings = find_undeclared(hits, declared)

    if args.json:
        _report_json(failures, warnings)
    else:
        _report_text(failures, warnings)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
