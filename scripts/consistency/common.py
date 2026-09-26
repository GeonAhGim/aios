"""CONSIST-1 공용 헬퍼 -- task-3725 CONSIST-1c로 check_consistency.py에서 분리.

검사군 모듈(wiring/contracts/time_money/spec_trace)이 공유하는 정적 분석
헬퍼(파일 순회, ast 파싱, ratchet-allow 판독, 모듈 상수 해석)만 담는다.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = ROOT / "consistency-baseline.json"

Hit = tuple[str, int]

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

_RATCHET_ALLOW_RE = re.compile(r"#\s*ratchet-allow:\s*(\S.*)")
_HEADER_SCAN_LINES = 20


@cache
def _iter_py_files(root: Path, subdir: str) -> list[Path]:
    # Nine call sites across wiring/contracts/time_money re-scan the same
    # "src" tree per run; caching turns the repeated rglob+scandir walk
    # (dominant cost of check_consistency.py) into a single walk (task-8000,
    # local CI "consistency" step timing out at 120s under load).
    base = root / subdir
    if not base.is_dir():
        return []
    out = []
    for path in base.rglob("*.py"):
        if _EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts[:-1]):
            continue
        out.append(path)
    return sorted(out)


@cache
def _read_text_cached(path: Path) -> str:
    # spec_trace also needs the raw source of every "src" file; sharing this
    # cache with _safe_parse avoids reading each src file a second time.
    return path.read_text(encoding="utf-8", errors="replace")


@cache
def _safe_parse(path: Path) -> ast.Module | None:
    # Same file is read+parsed by multiple independent checks (see above);
    # caching by path is safe because the working tree is not mutated during
    # a single check_consistency.py run.
    try:
        return ast.parse(_read_text_cached(path), filename=str(path))
    except SyntaxError:
        return None


def _ratchet_allow_reason(text: str) -> str | None:
    for line in text.splitlines()[:_HEADER_SCAN_LINES]:
        m = _RATCHET_ALLOW_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _module_level_literal_consts(tree: ast.Module) -> dict[str, list[str]]:
    """모듈 최상단 `NAME = "x"` / `NAME = ("a","b")` 대입만 정적으로 읽는다."""
    consts: dict[str, list[str]] = {}
    for node in ast.iter_child_nodes(tree):
        target: str | None = None
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            target, value = node.target.id, node.value
        if target is None or value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            consts[target] = [value.value]
        elif isinstance(value, ast.Tuple | ast.List):
            items = [
                e.value
                for e in value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if items and len(items) == len(value.elts):
                consts[target] = items
    return consts


def _resolve_str(node: ast.expr, consts: dict[str, list[str]]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        vals = consts.get(node.id)
        if vals and len(vals) == 1:
            return vals[0]
    return None


def _resolve_seq(
    node: ast.expr, consts: dict[str, list[str]], scope: dict[str, list[str]]
) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List):
        items = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        return items if items and len(items) == len(node.elts) else None
    if isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        return consts.get(node.id)
    return None


def _parse_env_example_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", stripped)
        if m:
            keys.add(m.group(1))
    return keys
