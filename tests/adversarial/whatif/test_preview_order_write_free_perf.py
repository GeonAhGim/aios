"""UX-10 성능 단언: AST 스캔 + import graph 스캔의 CPU 예산.

성능 예산: ADR-2026-09-09-C Decision 1 — "사전거래 게이트 p99 5ms" 행은
사전거래 체커(주문 전 게이트) 기준이고, import graph 스캔은 그와 별개
(전체 src 트래버스). 리뷰 기준 실측 p95 약 102ms. 여유 배수 2x로 200ms
예산 설정. ADR 행 추가 제안 중(사람 decision 대기).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §9 UX-10.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.perf_budget import PerfBudget

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "src/foundation/whatif/application/preview_order.py"

BANNED_MODULES: frozenset[str] = frozenset(
    {"asyncpg", "psycopg", "psycopg2", "sqlalchemy", "aiosqlite"}
)


def _is_write_verb_call(name: str) -> bool:
    if name in frozenset({"execute", "executemany"}):
        return True
    return any(
        name == verb or name.startswith(verb + "_")
        for verb in (
            "insert",
            "update",
            "delete",
            "save",
            "persist",
            "upsert",
            "commit",
            "write",
        )
    )


def find_direct_violations(tree: ast.AST) -> list[tuple[str, str]]:
    """AST가 파일 하나 안에서 직접 잡을 수 있는 것: async def/await/쓰기
    동사 호출. 순수 함수(파일 읽기만) — I/O 없음."""
    violations: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            violations.append(("async_def", node.name))
        elif isinstance(node, ast.Await):
            violations.append(("await", ast.dump(node)[:80]))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name is not None and _is_write_verb_call(name):
                violations.append(("write_call", name))
    return violations


def _imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if callee in frozenset({"__import__", "import_module"}) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.append(first.value)
    return names


def _matches_banned(module: str, banned: frozenset[str]) -> str | None:
    for name in banned:
        if module == name or module.startswith(name + "."):
            return name
    return None


def _module_to_path(module: str, root: Path) -> Path | None:
    parts = module.split(".")
    while parts:
        candidate = root.joinpath(*parts)
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            return candidate / "__init__.py"
        if candidate.with_suffix(".py").exists():
            return candidate.with_suffix(".py")
        parts.pop()
    return None


def scan_import_graph(
    start_file: Path, root: Path, banned: frozenset[str]
) -> tuple[list[tuple[str, str, tuple[str, ...]]], set[Path]]:
    """`start_file`에서 출발해 `src.*` import를 BFS로 추이적으로 따라가며
    `banned` 모듈 도달을 수집한다(R-56/CM-A2와 동형). 순수 함수."""
    from collections import deque

    parent: dict[Path, Path | None] = {start_file: None}
    queue: deque[Path] = deque([start_file])
    violations: list[tuple[str, str, tuple[str, ...]]] = []

    def chain(path: Path) -> tuple[str, ...]:
        links: list[str] = []
        cursor: Path | None = path
        while cursor is not None:
            links.append(cursor.relative_to(root).as_posix())
            cursor = parent[cursor]
        return tuple(reversed(links))

    while queue:
        path = queue.popleft()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_names(tree):
            hit = _matches_banned(module, banned)
            if hit is not None:
                violations.append((path.relative_to(root).as_posix(), module, chain(path)))
                continue
            if not module.startswith("src."):
                continue
            target = _module_to_path(module, root)
            if target is not None and target not in parent:
                parent[target] = path
                queue.append(target)
    return violations, set(parent)


# ---------------------------------------------------------------------------
# D2 성능 단언: AST 스캔 + import graph 스캔이 실제 파일 크기에 대해
# O(n) 이내로 완료되어야 한다.
# ---------------------------------------------------------------------------


def test_performance_ast_scan_under_10ms(perf_budget: PerfBudget) -> None:
    """AST scan CPU budget excludes unrelated xdist scheduling delays."""
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    perf_budget.assert_within(lambda: find_direct_violations(tree), budget_ms=10, batch=100)


def test_performance_import_scan_under_200ms(perf_budget: PerfBudget) -> None:
    """Measure the complete import graph with the unchanged 200ms CPU budget.

    Budget rationale: ADR-2026-09-09-C Decision 1 table has no row for
    'import graph scan'. The 'pre-trade gate p99 5ms' row targets the
    pre-trade gate checker (order-before execution), which is structurally
    different (full src tree traversal). Measured p95 ~102ms (review
    baseline). Budget 200ms = 2x margin. ADR row addition proposed;
    awaiting human decision — do not change this number yet.
    """

    def scan() -> None:
        violations, reached = scan_import_graph(TARGET, ROOT, BANNED_MODULES)
        assert not violations
        assert TARGET in reached and len(reached) > 1

    perf_budget.assert_within(scan, budget_ms=200)
