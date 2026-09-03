"""EO-06 정적 검사 — I-01 CI 게이트 도입.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §9 EO-06,
docs/design/INVARIANTS.md I-01("주문 제출·승인 경로의 어떤 생성자도 안전 게이트
인자를 Optional/None 기본값으로 받지 않는다").

AST로 `src/services/execution_loop/**`, `src/services/order_service/**`,
`src/api/execution_deps.py`의 **생성자(`__init__`)·팩토리(`make_*`/`get_*`
접두 최상위 함수)** 시그니처만 검사한다 — `submit_order`/`run_execution_tick`/
`is_submission_allowed`처럼 이미 만들어진 게이트를 인자로 받아 호출만 하는
일반 함수는 I-01의 대상이 아니다(그 함수들은 게이트가 없으면 그냥 통과시키는
게 아니라 "이 실행에 게이트를 연결할지" 조립부가 정하는 지점이라, `Optional`이
문제가 아니라 조립부가 `None`을 넘기는 것 자체가 문제 — 그게 바로 이 검사가
잡으려는 조립부, 즉 생성자/팩토리다).

이름에 `gate`/`monitor`/`lease`가 포함된 파라미터(대소문자 무시, `self`/`cls`
제외)가 다음 중 하나면 위반이다:
- 타입 애너테이션이 `Optional[...]` 또는 `... | None`
- 기본값이 리터럴 `None`

현재 코드에는 위반이 1건 있다 — `ExecutionLoopScheduler.__init__`의
`pre_submit_gate: PreSubmitGate | None = None`(`src/services/execution_loop/
scheduler.py`) — 이것이 정확히 이 스펙 문서 §1이 지적하는 P0-R2 배선 결함의
원인이다(`background_loops.py`가 이 기본값에 기대 게이트를 안 넘겨도 조용히
통과한다). EO-03이 이 시그니처를 필수 인자로 바꾸면 이 위반은 사라진다.

**strict 전환 조건**: 이 테스트는 지금 `xfail(strict=False)`로 도입한다(현재
알려진 결함을 곧바로 CI 빨간불로 만들지 않기 위해서다 — EO-03/EO-04가 아직
배정되지 않은 시점에 이 테스트만 추가하는 리프이기 때문). EO-04(`background_
loops.py` 조립부 수정)가 완료되어 `_scan_violations()`가 빈 리스트를 반환하는
것을 이 파일을 다시 실행해 확인하면, `xfail` 마커를 제거하고(또는
`strict=True`로 바꾸고) `test_no_optional_safety_gate_constructor_params`를
일반 통과 테스트로 전환한다 — 그래야 이후 누군가 같은 패턴의 결함(Optional
안전 게이트 기본값)을 재도입하면 이 테스트가 진짜로 CI를 막는다.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_TARGET_GLOBS = (
    "src/services/execution_loop/**/*.py",
    "src/services/order_service/**/*.py",
)
_TARGET_SINGLE_FILES = ("src/api/execution_deps.py",)

_GATE_NAME_MARKERS = ("gate", "monitor", "lease")
_CONSTRUCTOR_OR_FACTORY_NAMES = ("__init__",)
_FACTORY_PREFIXES = ("make_", "get_")

_NO_DEFAULT = object()


@dataclass(frozen=True)
class Violation:
    location: str
    function: str
    param: str
    reason: str

    def __str__(self) -> str:
        return f"{self.location}:{self.function}({self.param}) — {self.reason}"


def _dotted_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _annotation_violation_reason(annotation: ast.expr | None) -> str | None:
    if annotation is None:
        return None
    if isinstance(annotation, ast.Subscript) and _dotted_name(annotation.value) in (
        "Optional",
        "typing.Optional",
    ):
        return "Optional[...] 애너테이션"
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        for side in (annotation.left, annotation.right):
            if isinstance(side, ast.Constant) and side.value is None:
                return "`... | None` 애너테이션"
    return None


def _is_constructor_or_factory(
    node: ast.FunctionDef | ast.AsyncFunctionDef, in_class: bool
) -> bool:
    if in_class and node.name in _CONSTRUCTOR_OR_FACTORY_NAMES:
        return True
    return node.name.startswith(_FACTORY_PREFIXES)


def _iter_params_with_defaults(
    args: ast.arguments,
) -> list[tuple[ast.arg, object]]:
    positional = [*args.posonlyargs, *args.args]
    n_required = len(positional) - len(args.defaults)
    pairs: list[tuple[ast.arg, object]] = [
        (arg, _NO_DEFAULT if i < n_required else args.defaults[i - n_required])
        for i, arg in enumerate(positional)
    ]
    pairs.extend(
        (arg, default if default is not None else _NO_DEFAULT)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    )
    return pairs


def _scan_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, location: str, qualname_prefix: str
) -> list[Violation]:
    violations = []
    qualname = f"{qualname_prefix}{node.name}"
    for arg, default in _iter_params_with_defaults(node.args):
        if arg.arg in ("self", "cls"):
            continue
        if not any(marker in arg.arg.lower() for marker in _GATE_NAME_MARKERS):
            continue
        reason = _annotation_violation_reason(arg.annotation)
        if reason is None and default is not _NO_DEFAULT:
            if isinstance(default, ast.Constant) and default.value is None:
                reason = "기본값 None"
        if reason is not None:
            violations.append(Violation(location, qualname, arg.arg, reason))
    return violations


def _scan_source(source: str, location: str) -> list[Violation]:
    tree = ast.parse(source)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(
                    child, ast.FunctionDef | ast.AsyncFunctionDef
                ) and _is_constructor_or_factory(child, in_class=True):
                    violations.extend(_scan_function(child, location, f"{node.name}."))
        elif isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and _is_constructor_or_factory(node, in_class=False):
            violations.extend(_scan_function(node, location, ""))
    return violations


def _iter_target_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in _TARGET_GLOBS:
        files.update(_REPO_ROOT.glob(pattern))
    for rel in _TARGET_SINGLE_FILES:
        files.add(_REPO_ROOT / rel)
    return sorted(f for f in files if f.is_file())


def _scan_violations() -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_target_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        violations.extend(_scan_source(path.read_text(encoding="utf-8"), rel))
    return violations


# --- 스캐너 자체의 정확성 검증 (negative test 포함) -------------------------


def test_scanner_flags_optional_union_default_in_constructor():
    source = (
        "class Scheduler:\n"
        "    def __init__(self, pre_submit_gate: str | None = None) -> None:\n"
        "        pass\n"
    )
    violations = _scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:Scheduler.__init__(pre_submit_gate) — `... | None` 애너테이션"
    ]


def test_scanner_flags_typing_optional_annotation_in_factory():
    source = (
        "from typing import Optional\n\n"
        "def make_gate(pool) -> Optional[int]:\n"
        "    pass\n"
    )
    violations = _scan_source(source, "fixture.py")
    assert len(violations) == 0  # 반환 타입이 아니라 파라미터만 검사 대상이다

    source_with_param = (
        "from typing import Optional\n\n"
        "def make_gate(pool, distrust_monitor: Optional[int] = None):\n"
        "    pass\n"
    )
    violations = _scan_source(source_with_param, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:make_gate(distrust_monitor) — Optional[...] 애너테이션"
    ]


def test_scanner_ignores_required_gate_param_without_none_default():
    """negative test — 안전 게이트 인자가 필수(기본값 없음)면 위반이 아니다."""
    source = (
        "class Scheduler:\n"
        "    def __init__(self, pre_submit_gate: str) -> None:\n"
        "        pass\n"
    )
    assert _scan_source(source, "fixture.py") == []


def test_scanner_ignores_non_constructor_non_factory_functions():
    """negative test — 이미 만들어진 게이트를 받아 호출만 하는 일반 함수(예:
    submit_order/run_execution_tick 패턴)는 I-01 대상이 아니므로 위반이 아니다."""
    source = (
        "async def is_submission_allowed(pre_submit_gate: str | None, *, x: int) -> bool:\n"
        "    pass\n"
    )
    assert _scan_source(source, "fixture.py") == []


# --- 실제 배선 코드 검사 -----------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "EO-06 도입 시점(task-1115) 기준 알려진 위반 1건: "
        "src/services/execution_loop/scheduler.py:ExecutionLoopScheduler.__init__"
        "(pre_submit_gate) — EO-03/EO-04가 고칠 대상. strict 전환 조건은 이 "
        "파일 모듈 docstring 참조."
    ),
)
def test_no_optional_safety_gate_constructor_params():
    violations = _scan_violations()
    assert violations == [], "\n".join(str(v) for v in violations)
