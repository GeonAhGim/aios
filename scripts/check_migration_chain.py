"""alembic 마이그레이션 체인 정적 검사 — PLT-38.

`src/db/migrations/versions/*.py`를 AST로만 읽어(import·실행하지 않음)
`revision`/`down_revision` 모듈 레벨 대입을 추출하고 다음을 검사한다:

1. head(자신을 down_revision으로 참조하는 다른 리비전이 없는 리비전)가
   2개 이상이면 실패 — alembic이 `upgrade head`에서 어느 head인지 모호해진다.
2. `down_revision`이 존재하지 않는 리비전 id를 가리키면 실패(체인 끊김).
3. `down_revision` 순환 참조가 있으면 실패.

DB에 연결하지 않는다 — CI worktree에 DB가 없어도 동작해야 한다(PLT-38 decision).
사용: `python scripts/check_migration_chain.py`. 종료코드 0=통과.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSIONS_DIR = ROOT / "src" / "db" / "migrations" / "versions"


@dataclass(frozen=True)
class RevisionRecord:
    revision: str
    down_revisions: tuple[str, ...]
    path: Path


def _literal_str_values(node: ast.AST | None) -> tuple[str, ...] | None:
    """Constant 문자열, `None`, 혹은 문자열로만 이뤄진 tuple/list 리터럴만 인식한다.
    그 외(함수 호출, 변수 참조 등)는 정적으로 판단할 수 없으므로 None을 반환한다."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        if node.value is None:
            return ()
        if isinstance(node.value, str):
            return (node.value,)
        return None
    if isinstance(node, (ast.Tuple, ast.List)):
        values: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                return None
        return tuple(values)
    return None


def parse_revision_file(path: Path) -> RevisionRecord | None:
    """리비전 파일에서 `revision`/`down_revision` 모듈 레벨 대입만 정적으로 읽는다.
    파일을 import하지 않으므로 `upgrade()`/`downgrade()` 안의 op 호출은 실행되지 않는다."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None

    revision: str | None = None
    down_revisions: tuple[str, ...] | None = None

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = node.targets
            value: ast.AST | None = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue

        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "revision":
                values = _literal_str_values(value)
                if values:
                    revision = values[0]
            elif target.id == "down_revision":
                down_revisions = _literal_str_values(value)

    if revision is None:
        return None
    return RevisionRecord(revision=revision, down_revisions=down_revisions or (), path=path)


def _iter_revision_files(versions_dir: Path) -> list[Path]:
    return sorted(p for p in versions_dir.glob("*.py") if p.name != "__init__.py")


def _find_cycle(records: dict[str, RevisionRecord]) -> list[str] | None:
    known = set(records)
    done: set[str] = set()

    def dfs(node: str, path: list[str]) -> list[str] | None:
        if node in done:
            return None
        if node in path:
            return [*path[path.index(node) :], node]
        path.append(node)
        for parent in records[node].down_revisions:
            if parent in known:
                cycle = dfs(parent, path)
                if cycle:
                    return cycle
        path.pop()
        done.add(node)
        return None

    for revision_id in records:
        cycle = dfs(revision_id, [])
        if cycle:
            return cycle
    return None


def find_chain_issues(versions_dir: Path) -> list[str]:
    """마이그레이션 체인 문제를 문자열 목록으로 반환한다. 빈 목록이면 통과."""
    issues: list[str] = []
    records: dict[str, RevisionRecord] = {}
    duplicate_paths: dict[str, list[Path]] = {}

    for path in _iter_revision_files(versions_dir):
        record = parse_revision_file(path)
        if record is None:
            issues.append(f"revision 식별 불가(정적 파싱 실패): {path.name}")
            continue
        if record.revision in records:
            duplicate_paths.setdefault(
                record.revision, [records[record.revision].path]
            ).append(path)
            continue
        records[record.revision] = record

    for revision_id, paths in duplicate_paths.items():
        names = ", ".join(p.name for p in paths)
        issues.append(f"중복 revision id: {revision_id} ({names})")

    known = set(records)
    for record in records.values():
        for parent in record.down_revisions:
            if parent not in known:
                issues.append(
                    f"down_revision 끊김: {record.path.name} → '{parent}' 리비전이 존재하지 않음"
                )

    cycle = _find_cycle(records)
    if cycle:
        issues.append(f"down_revision 순환 참조: {' → '.join(cycle)}")

    referenced_as_parent = {
        parent for record in records.values() for parent in record.down_revisions if parent in known
    }
    heads = sorted(known - referenced_as_parent)
    if len(heads) >= 2:
        head_files = ", ".join(f"{h}({records[h].path.name})" for h in heads)
        issues.append(f"다중 head({len(heads)}개) — alembic upgrade head가 모호함: {head_files}")
    elif len(heads) == 0 and known and cycle is None:
        issues.append("head를 찾을 수 없음")

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="alembic 마이그레이션 체인 정적 검사(PLT-38)")
    parser.add_argument("--versions-dir", type=Path, default=DEFAULT_VERSIONS_DIR)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    if not args.versions_dir.is_dir():
        print(f"FAIL: {args.versions_dir} 없음")
        return 1

    issues = find_chain_issues(args.versions_dir)
    if issues:
        print("FAIL: 마이그레이션 체인 검사 실패")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    print(f"OK: 마이그레이션 체인 검사 통과 ({args.versions_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
