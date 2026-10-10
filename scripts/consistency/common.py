"""CONSIST-1 공용 헬퍼 -- task-3725 CONSIST-1c로 check_consistency.py에서 분리.

검사군 모듈(wiring/contracts/time_money/spec_trace)이 공유하는 정적 분석
헬퍼(파일 순회, ast 파싱, ratchet-allow 판독, 모듈 상수 해석)만 담는다.
"""

from __future__ import annotations

import ast
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path


# esc-ci-prepare ([prepare] FileNotFoundError(2, ..., None, 2, None) / earlier
# RuntimeError "head_sha: origin/main 해석 실패 rc=3221225794" STATUS_DLL_INIT_FAILED):
# task-8949 added this pool at 32 workers -- higher than even the
# ThreadPoolExecutor library default (min(32, cpu_count+4)=28) that check_no_bom.py's
# SCAN_WORKERS saga (task-8639/8657/8667/9156) already proved storms this shared,
# antivirus-scanned fleet box: a burst of concurrent file-open threads from one
# lane's step starves sibling lanes' process-creation (git.exe/python.exe spawn
# fails with STATUS_DLL_INIT_FAILED or, with different OS-level timing, a bare
# FileNotFoundError out of CreateProcess). check_no_bom.py/check_code_ratchets.py/
# check_import_linter.py were all retuned to the fleet-safe value of 16 and given
# an AIOS_CI_SCAN_WORKERS override (task-9259/task-11489) the same week, but this
# consistency-check pool was never brought in line -- it kept running at 32,
# nearly double the proven-unsafe 28, every time `check_consistency.py` ran.
# Matching the other three gates' value and override closes that gap without
# raising any budget/baseline (DECISION_GUIDELINES B-2).
def _resolve_scan_workers(default: int) -> int:
    raw = os.environ.get("AIOS_CI_SCAN_WORKERS")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


_PRIME_MAX_WORKERS = _resolve_scan_workers(16)

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


@cache
def _walked_nodes(path: Path) -> tuple[ast.AST, ...]:
    # naive_datetime/money_float/symbol_id_assembly/authority_duplication
    # (time_money.py), env_key/feature_flag (contracts.py) and
    # port_method_unimplemented (wiring.py) each ran their own full
    # `ast.walk(tree)` over every "src" file -- 8 independent BFS walks of the
    # same ~1,600 files per run. Profiling esc-ci-consistency's recurring
    # 120s-timeout locally showed ast.walk/iter_child_nodes as the dominant
    # cost once file I/O was already deduped by _safe_parse/_read_text_cached
    # (task-8949). Walking once per file and caching the flattened node list
    # lets every later check reuse it instead of re-walking the same tree.
    tree = _safe_parse(path)
    if tree is None:
        return ()
    return tuple(ast.walk(tree))


def _prime_py_file_cache(root: Path, subdir: str) -> None:
    """`subdir` 아래 모든 `.py` 파일을 스레드로 겹쳐 읽어 `_safe_parse`/
    `_read_text_cached` 캐시를 미리 채운다.

    blocking `open()`/`read()` 중에는 GIL이 풀리므로, 파일마다 순차로 열던
    것을 스레드풀로 겹치면 Windows I/O 지연(파일당 관측된 dominant cost,
    부하 시 편차가 큼)이 벽시계 시간에서 상당 부분 겹쳐진다(task-8949,
    esc-ci-consistency의 120s 타임아웃 재발 완화). `_safe_parse`는 내부에서
    `_read_text_cached`를 호출하므로 이거 하나만 미리 돌리면 두 캐시가 함께
    채워진다. `functools.cache`는 실제 함수 호출을 락 밖에서 수행하므로 다른
    인자에 대한 동시 호출은 참으로 겹쳐 실행된다.
    """
    paths = _iter_py_files(root, subdir)
    if not paths:
        return
    with ThreadPoolExecutor(max_workers=_PRIME_MAX_WORKERS) as pool:
        list(pool.map(_safe_parse, paths))


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
