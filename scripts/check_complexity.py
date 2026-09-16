"""RATCHET-2 인지 복잡도(cognitive complexity) 게이트 -- ADR-2026-09-10-C, task-3256.

`src/`, `scripts/`의 모든 함수(중첩 함수·메서드 포함, 각각 독립적으로 채점)에 대해
SonarSource 인지 복잡도 규칙을 단순화한 근사치를 AST로 계산한다:

  * if/elif/else, for/while, except, 삼항(IfExp), match-case -- 진입 시 +1, 추가로
    현재 중첩 깊이만큼 가산(중첩할수록 더 무겁게). elif는 그 자체로 +1이지만 중첩을
    더 깊이지 않는다(형제 분기이지 하위 분기가 아니므로).
  * and/or 체인(`BoolOp`) -- 체인 하나당 +1(체인 내 개수와 무관, 단순화).
  * 재귀 호출(자기 자신 이름을 직접 호출) -- +1.
  * 중첩 함수/람다 -- 자신의 복잡도에는 가산하지 않는다(그 자체가 독립된 채점
    단위이므로, 이중 계산 방지).

정확한 SonarSource 스펙을 그대로 이식한 것은 아니다(`check_consistency.py`와 같은
원칙 -- "완벽한 판정을 목표하지 않는다", baseline이 과탐을 흡수한다). 상한은
`CAP`(25) -- 이를 초과하는 함수 수(`over_cap_count`)를 `check_code_ratchets.py`와
동일한 래칫 방식으로 baseline 대비 추적한다: 늘면 exit 2, 줄어도 `--update` 없이는
baseline을 건드리지 않는다.

대상은 `src/`, `scripts/`만 -- `tests/`는 매개변수화 픽스처 등으로 분기가 흔히
정당화되므로 이 게이트의 대상이 아니다(OPS-42: 신규 게이트는 warn 모드 +
baseline으로 등록, `C:/aios/pm`의 `ci_recheck.STEP_MODE`에서 승격 전까지는 gate가
아니다).

사용: `python scripts/check_complexity.py [--update]` (저장소 루트에서).
종료코드: 0 = 통과, 2 = over_cap_count가 baseline보다 증가, 1 = 입력 오류.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "complexity-baseline.json"
DEFAULT_SUBDIRS = ("src", "scripts")
CAP = 25

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

METRICS = ("over_cap_count",)

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
Hit = tuple[str, int, str, int]  # (rel_path, lineno, qualname, score)


class ComplexityError(ValueError):
    """baseline JSON 형식 오류."""


def _iter_python_files(root: Path, subdirs: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if _EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts[:-1]):
                continue
            files.append(path)
    return sorted(files)


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _function_defs(tree: ast.Module) -> list[FunctionNode]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def cognitive_complexity(func: FunctionNode) -> int:
    """단일 함수(중첩 함수 제외)의 인지 복잡도 근사치를 계산한다."""
    score = 0

    def visit_orelse(orelse: list[ast.stmt], nesting: int) -> None:
        nonlocal score
        if not orelse:
            return
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            elif_node = orelse[0]
            score += 1
            visit(elif_node.test, nesting)
            for stmt in elif_node.body:
                visit(stmt, nesting + 1)
            visit_orelse(elif_node.orelse, nesting)
        else:
            score += 1
            for stmt in orelse:
                visit(stmt, nesting + 1)

    def visit(node: ast.AST, nesting: int) -> None:
        """`node` 자신의 종류를 먼저 판정한 뒤(가산), 자식은 알맞은 중첩 깊이로
        재귀한다 -- 자식만 검사하고 진입 노드 자신은 건너뛰던 버그(모든 최상위
        구조가 채점되지 않던 원인)를 피하기 위해 진입점 포함 모든 호출이 이
        하나의 함수를 거친다."""
        nonlocal score
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            return
        if isinstance(node, ast.If):
            score += 1 + nesting
            visit(node.test, nesting)
            for stmt in node.body:
                visit(stmt, nesting + 1)
            visit_orelse(node.orelse, nesting)
            return
        if isinstance(node, ast.For | ast.AsyncFor | ast.While):
            score += 1 + nesting
            for child in ast.iter_child_nodes(node):
                visit(child, nesting + 1)
            return
        if isinstance(node, ast.ExceptHandler):
            score += 1 + nesting
            for child in ast.iter_child_nodes(node):
                visit(child, nesting + 1)
            return
        if isinstance(node, ast.IfExp):
            score += 1 + nesting
            for child in ast.iter_child_nodes(node):
                visit(child, nesting + 1)
            return
        if isinstance(node, ast.BoolOp):
            score += 1
            for child in ast.iter_child_nodes(node):
                visit(child, nesting)
            return
        if hasattr(ast, "Match") and isinstance(node, ast.Match):
            for case in node.cases:
                score += 1 + nesting
                for stmt in case.body:
                    visit(stmt, nesting + 1)
            return
        if isinstance(node, ast.Call) and _callee_name(node.func) == func.name:
            score += 1
            for child in ast.iter_child_nodes(node):
                visit(child, nesting)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, nesting)

    for stmt in func.body:
        visit(stmt, 0)
    return score


def _qualname(path_stack: list[str], func: FunctionNode) -> str:
    return ".".join([*path_stack, func.name])


def scan_file(rel: str, text: str) -> list[Hit]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits: list[Hit] = []
    for func in _function_defs(tree):
        score = cognitive_complexity(func)
        if score > CAP:
            hits.append((rel, func.lineno, func.name, score))
    return hits


def scan_tree(root: Path, subdirs: tuple[str, ...] = DEFAULT_SUBDIRS) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_python_files(root, subdirs):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        hits.extend(scan_file(rel, text))
    hits.sort()
    return hits


def counts_of(hits: list[Hit]) -> dict[str, int]:
    return {"over_cap_count": len(hits)}


def read_baseline(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ComplexityError(f"baseline 파일이 비어 있음: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ComplexityError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ComplexityError("baseline JSON은 객체여야 함")
    result: dict[str, int] = {}
    for metric in METRICS:
        value = data.get(metric)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ComplexityError(f"baseline 값이 정수가 아님: {metric}={value!r}")
        result[metric] = value
    return result


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    payload = {metric: counts[metric] for metric in METRICS}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    parser.add_argument("--top", type=int, default=10, help="증가 시 보여줄 위반 목록 개수")
    args = parser.parse_args(argv)

    try:
        baseline = read_baseline(args.baseline)
    except ComplexityError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_tree(args.root)
    current = counts_of(hits)

    if baseline is None:
        write_baseline(args.baseline, current)
        print(f"BASELINE 초기화: {current} -> {args.baseline}")
        return 0

    increased = {m: (baseline[m], current[m]) for m in METRICS if current[m] > baseline[m]}
    if increased:
        for metric, (before, after) in increased.items():
            print(f"FAIL: {metric} {before}개 -> {after}개 (증가, CAP={CAP})")
            for rel, lineno, name, score in hits[: args.top]:
                print(f"    {rel}:{lineno} {name}() complexity={score}")
        return 2

    decreased = {m for m in METRICS if current[m] < baseline[m]}
    if decreased and args.update:
        write_baseline(args.baseline, current)
        print(f"OK: 감소, baseline 갱신 {baseline} -> {current}")
        return 0

    if decreased:
        print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline} (현재 {current})")
        return 0

    print(f"OK: {current} (baseline {baseline}, CAP={CAP})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
