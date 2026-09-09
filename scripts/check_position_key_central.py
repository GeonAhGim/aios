"""FA-0d 정적 검사 -- "position_key를 f-string/concat/join으로 직접 조립"하는
형태를 AST로 잡는다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(§9 표 113행, "portfolio_id 편입 + 중앙 생성자 + 호출자 전수 경유 정적 검사").
`scripts/check_entity_context.py`와 같은 관례의 AST 스캐너다.

`domain/position_key.py`(`PositionKey`, `.parse()`)가 `position_key` 문자열의
유일한 합법적 생성 경로다 -- 이 스크립트는 그 경로를 우회해 `position_key`라는
이름의 변수/키워드 인자에 f-string(`JoinedStr`)·문자열 결합(`+`/`%`)·
`"...".join(...)` 호출로 값을 직접 대입하는 코드를 찾는다. `PositionKey(...)`나
`PositionKey.parse(...)`를 (직접 또는 `str(...)`로 감싸) 대입하는 것,
기존 값을 그대로 통과시키는 것(`Name`/`Attribute`/`Subscript`)은 위반이 아니다.

스캔 대상: `src/` 전체(정적 검사 대상은 애플리케이션 코드다) 중
`domain/position_key.py`(중앙 생성자 자신, 예외)와 `db/migrations/`(과거
시점 raw 문자열을 다루는 1회성 백필 -- 도메인 객체가 존재하기 전/형식이
바뀌는 순간의 값을 다루므로 이 규칙의 대상이 아니다, task-1943 마이그레이션
`cdb114b6903f`가 실제로 이런 이유로 `new_key`라는 다른 이름을 쓴다)는
제외한다.

등록 전 위반 수를 세고(ADR-2026-09-06-G §1), 0이 아니면 baseline으로 허용하지
않고 수정 대상으로 삼는다(task-1943 PM decision) -- 이 스크립트는 exempt
목록을 두지 않는다.

사용: `python scripts/check_position_key_central.py` (저장소 루트에서).
종료코드 0=위반 없음, 1=위반 있음(각 위반을 표준출력에 나열).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOT = "src"
_EXEMPT_PATH_PARTS = (
    ("src", "foundation", "positions", "domain", "position_key.py"),
)
_EXEMPT_DIR_PARTS = (("src", "db", "migrations"),)
_TARGET_NAME = "position_key"


@dataclass(frozen=True)
class Violation:
    location: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.location}:{self.line} — {self.reason}"


def _is_assembly_expr(node: ast.expr) -> str | None:
    """직접 조립 형태면 사유 문자열을, 아니면 None을 돌려준다."""
    if isinstance(node, ast.JoinedStr):
        return "f-string으로 직접 조립"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return "문자열 결합(+/%)으로 직접 조립"
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
    ):
        return '"...".join(...)으로 직접 조립'
    return None


def _scan_source(source: str, location: str) -> list[Violation]:
    tree = ast.parse(source)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        lineno = 0
        if isinstance(node, ast.Assign):
            targets, value, lineno = node.targets, node.value, node.lineno
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value, lineno = [node.target], node.value, node.lineno
        if value is not None:
            for target in targets:
                if isinstance(target, ast.Name) and target.id == _TARGET_NAME:
                    reason = _is_assembly_expr(value)
                    if reason is not None:
                        violations.append(Violation(location, lineno, reason))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == _TARGET_NAME:
                    reason = _is_assembly_expr(kw.value)
                    if reason is not None:
                        violations.append(Violation(location, kw.value.lineno, reason))
    return violations


def _is_exempt(rel_parts: tuple[str, ...]) -> bool:
    if rel_parts in _EXEMPT_PATH_PARTS:
        return True
    return any(rel_parts[: len(prefix)] == prefix for prefix in _EXEMPT_DIR_PARTS)


def scan_violations() -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted((_REPO_ROOT / _SCAN_ROOT).rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT)
        if _is_exempt(rel.parts):
            continue
        violations.extend(_scan_source(path.read_text(encoding="utf-8"), str(rel)))
    return violations


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    violations = scan_violations()
    if violations:
        for v in violations:
            print(str(v))
        print(f"check_position_key_central: {len(violations)}건 위반")
        return 1
    print("check_position_key_central: 위반 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
