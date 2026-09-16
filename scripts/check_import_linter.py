"""RATCHET-2 의존 방향 게이트 -- ADR-2026-09-10-C, task-3256.

`.importlinter`(이 저장소 전용 최소 포맷, 상단 주석 참조)에 선언된 3종 계약을
`src/`의 임포트 그래프에 대해 정적으로 검사한다:

  1. forbidden        -- src/core(순수 도메인, 무 I/O)가 adapter/exchange/api/
                          foundation 계층을 임포트하면 위반
  2. forbidden_suffix  -- 같은 foundation 애그리게잇 안에서 domain/이 adapters/를
                          임포트하면 위반(역의존 금지)
  3. boundary          -- foundation 애그리게잇이 다른 애그리게잇의 domain/adapters
                          내부로 직접 들어가면 위반(교차 애그리게잇 경계)
  4. cycles            -- src/ 임포트 그래프에 순환이 있으면 위반

`check_code_ratchets.py`와 같은 래칫 방식: `import-linter-baseline.json`에 계약별
현재 위반 수를 기록하고, 늘어나면 exit 2. 임포트만 AST로 읽으며 실제 import는
수행하지 않는다(DB·네트워크 없음).

사용: `python scripts/check_import_linter.py [--update]` (저장소 루트에서).
종료코드: 0 = 통과, 2 = 위반 수 증가, 1 = 입력 오류.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACTS = ROOT / ".importlinter"
DEFAULT_BASELINE = ROOT / "import-linter-baseline.json"
SCAN_SUBDIR = "src"

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

Hit = tuple[str, int, str]  # (rel_path, lineno, detail)


class ImportLinterError(ValueError):
    """계약/baseline 파일 형식 오류."""


# ---------------------------------------------------------------------------
# 임포트 그래프
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


def _module_name(root: Path, path: Path) -> tuple[str, bool]:
    """(dotted module name, is_package) -- __init__.py는 패키지 자신을 가리킨다."""
    rel_parts = list(path.relative_to(root).with_suffix("").parts)
    is_package = rel_parts[-1] == "__init__"
    if is_package:
        rel_parts = rel_parts[:-1]
    return ".".join(rel_parts), is_package


def _resolve_relative(module_dotted: str, is_package: bool, level: int, module: str | None) -> str:
    package_parts = module_dotted.split(".") if is_package else module_dotted.split(".")[:-1]
    if level > 1:
        package_parts = package_parts[: len(package_parts) - (level - 1)]
    if module:
        package_parts = [*package_parts, *module.split(".")]
    return ".".join(p for p in package_parts if p)


def _imports_of(path: Path, module_dotted: str, is_package: bool) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:
        return set()
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _resolve_relative(module_dotted, is_package, node.level, node.module)
                if base:
                    targets.add(base)
                    for alias in node.names:
                        if alias.name != "*":
                            targets.add(f"{base}.{alias.name}")
            elif node.module:
                targets.add(node.module)
                if node.module == "src" or node.module.startswith("src."):
                    for alias in node.names:
                        if alias.name != "*":
                            targets.add(f"{node.module}.{alias.name}")
    return targets


def build_graph(root: Path, subdir: str = SCAN_SUBDIR) -> dict[str, set[str]]:
    """모듈 dotted name -> 그 모듈이 직접 임포트하는 dotted name 집합."""
    graph: dict[str, set[str]] = {}
    for path in _iter_python_files(root, subdir):
        module_dotted, is_package = _module_name(root, path)
        graph[module_dotted] = _imports_of(path, module_dotted, is_package)
    return graph


def _module_path_hits(root: Path, subdir: str = SCAN_SUBDIR) -> dict[str, tuple[Path, int]]:
    """모듈 dotted name -> (파일 경로, 첫 import 문의 줄번호가 아니라 파일 시작줄 1)."""
    out: dict[str, tuple[Path, int]] = {}
    for path in _iter_python_files(root, subdir):
        module_dotted, _ = _module_name(root, path)
        out[module_dotted] = (path, 1)
    return out


# ---------------------------------------------------------------------------
# 계약 파일 파싱
# ---------------------------------------------------------------------------


def _split_lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    """옵션 이름(dotted module path)을 소문자로 접지 않는 ConfigParser --
    기본 optionxform은 대소문자를 뭉개는데, 모듈 경로는 대소문자를 보존해야
    한다(현재 이 파일의 모든 옵션 이름이 소문자라 실질적 영향은 없지만,
    `parser.optionxform = str` 방식의 메서드 재대입은 mypy가 막아 `# type:
    ignore`가 필요해진다 -- type-ignore 예산(check_type_ignore_budget.py)을
    쓰지 않도록 서브클래싱으로 대체)."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def parse_contracts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ImportLinterError(f"계약 파일이 없음: {path}")
    parser = _CaseSensitiveConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise ImportLinterError(f".importlinter 파싱 실패: {exc}") from exc

    contracts: list[dict[str, Any]] = []
    for section in parser.sections():
        if ":" not in section:
            raise ImportLinterError(f"섹션 이름 형식 오류(<type>:<id> 필요): {section}")
        kind, contract_id = section.split(":", 1)
        entries = dict(parser.items(section))
        contract = {"id": contract_id, "kind": kind, "raw": entries}
        if kind == "forbidden":
            contract["source"] = entries["source"].strip()
            contract["forbidden"] = _split_lines(entries.get("forbidden", ""))
        elif kind == "forbidden_suffix":
            contract["root"] = entries["root"].strip()
            contract["source_suffix"] = entries["source_suffix"].strip()
            contract["forbidden_suffix"] = entries["forbidden_suffix"].strip()
        elif kind == "boundary":
            contract["root"] = entries["root"].strip()
            contract["internal_suffixes"] = _split_lines(entries.get("internal_suffixes", ""))
        elif kind == "cycles":
            contract["root"] = entries["root"].strip()
        else:
            raise ImportLinterError(f"알 수 없는 계약 type: {kind} ({section})")
        contracts.append(contract)
    return contracts


# ---------------------------------------------------------------------------
# 계약 평가
# ---------------------------------------------------------------------------


def _starts_with(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _eval_forbidden(graph: dict[str, set[str]], contract: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    source, forbidden = contract["source"], contract["forbidden"]
    for module, targets in graph.items():
        if not _starts_with(module, source):
            continue
        for target in targets:
            if any(_starts_with(target, f) for f in forbidden):
                hits.append((module, 0, f"{module} -> {target} (forbidden: {source} -> {target})"))
    return hits


def _aggregate_of(module: str, root: str) -> str | None:
    if not _starts_with(module, root):
        return None
    rest = module[len(root) :].lstrip(".")
    if not rest:
        return None
    return rest.split(".", 1)[0]


def _eval_forbidden_suffix(graph: dict[str, set[str]], contract: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    root = contract["root"]
    src_suffix, dst_suffix = contract["source_suffix"], contract["forbidden_suffix"]
    for module, targets in graph.items():
        agg = _aggregate_of(module, root)
        if agg is None:
            continue
        mod_parts = module.split(".")
        if src_suffix not in mod_parts:
            continue
        for target in targets:
            if _aggregate_of(target, root) != agg:
                continue
            if dst_suffix in target.split("."):
                hits.append(
                    (module, 0, f"{module} -> {target} (domain->adapters 역의존, agg={agg})")
                )
    return hits


def _eval_boundary(graph: dict[str, set[str]], contract: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    root = contract["root"]
    internal = set(contract["internal_suffixes"])
    for module, targets in graph.items():
        agg = _aggregate_of(module, root)
        if agg is None:
            continue
        for target in targets:
            target_agg = _aggregate_of(target, root)
            if target_agg is None or target_agg == agg:
                continue
            target_parts = target.split(".")
            if internal & set(target_parts):
                hits.append(
                    (
                        module,
                        0,
                        f"{module} -> {target} (foundation 경계 위반: {agg} -> {target_agg})",
                    )
                )
    return hits


def _eval_cycles(graph: dict[str, set[str]], contract: dict[str, Any]) -> list[Hit]:
    root = contract["root"]
    scoped = {
        m: {t for t in ts if _starts_with(t, root)}
        for m, ts in graph.items()
        if _starts_with(m, root)
    }

    hits: list[Hit] = []
    seen_cycles: set[frozenset[str]] = set()
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(scoped, WHITE)
    stack: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        stack.append(node)
        for neighbor in sorted(scoped.get(node, ())):
            if neighbor not in scoped:
                continue
            if color.get(neighbor, WHITE) == WHITE:
                dfs(neighbor)
            elif color.get(neighbor) == GRAY:
                cycle_start = stack.index(neighbor)
                cycle = stack[cycle_start:]
                key = frozenset(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    hits.append((cycle[0], 0, "순환: " + " -> ".join([*cycle, cycle[0]])))
        stack.pop()
        color[node] = BLACK

    for node in sorted(scoped):
        if color[node] == WHITE:
            dfs(node)
    return hits


_EVALUATORS = {
    "forbidden": _eval_forbidden,
    "forbidden_suffix": _eval_forbidden_suffix,
    "boundary": _eval_boundary,
    "cycles": _eval_cycles,
}


def evaluate_contracts(root: Path, contracts: list[dict[str, Any]]) -> dict[str, list[Hit]]:
    graph = build_graph(root)
    result: dict[str, list[Hit]] = {}
    for contract in contracts:
        evaluator = _EVALUATORS[contract["kind"]]
        result[contract["id"]] = sorted(set(evaluator(graph, contract)))
    return result


# ---------------------------------------------------------------------------
# 래칫 · CLI
# ---------------------------------------------------------------------------


def counts_of(hits: dict[str, list[Hit]]) -> dict[str, int]:
    return {contract_id: len(h) for contract_id, h in hits.items()}


def read_baseline(path: Path, metric_ids: list[str]) -> dict[str, int] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ImportLinterError(f"baseline 파일이 비어 있음: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportLinterError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ImportLinterError("baseline JSON은 객체여야 함")
    result: dict[str, int] = {}
    for metric in metric_ids:
        value = data.get(metric)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ImportLinterError(f"baseline 값이 정수가 아님: {metric}={value!r}")
        result[metric] = value
    return result


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    path.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    parser.add_argument("--top", type=int, default=10, help="증가 시 보여줄 위반 목록 개수")
    args = parser.parse_args(argv)

    try:
        contracts = parse_contracts(args.contracts)
        hits = evaluate_contracts(args.root, contracts)
        metric_ids = [c["id"] for c in contracts]
        baseline = read_baseline(args.baseline, metric_ids)
    except (ImportLinterError, KeyError) as exc:
        print(f"FAIL: {exc}")
        return 1

    current = counts_of(hits)

    if baseline is None:
        write_baseline(args.baseline, current)
        print(f"BASELINE 초기화: {current} -> {args.baseline}")
        return 0

    increased = {m: (baseline[m], current[m]) for m in metric_ids if current[m] > baseline[m]}
    if increased:
        for metric, (before, after) in increased.items():
            print(f"FAIL: {metric} {before}개 -> {after}개 (증가)")
            for _, _, detail in hits[metric][: args.top]:
                print(f"    {detail}")
        return 2

    decreased = {m for m in metric_ids if current[m] < baseline[m]}
    if decreased and args.update:
        write_baseline(args.baseline, current)
        print(f"OK: 감소, baseline 갱신 {baseline} -> {current}")
        return 0

    if decreased:
        print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline} (현재 {current})")
        return 0

    print(f"OK: {current} (baseline {baseline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
