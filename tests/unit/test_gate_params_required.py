"""EO-06 정적 검사 — I-01 CI 게이트 도입.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §9 EO-06,
docs/design/INVARIANTS.md I-01("주문 제출·승인 경로의 어떤 생성자도 안전 게이트
인자를 Optional/None 기본값으로 받지 않는다").

AST로 아래 대상을 검사한다:
- `src/services/execution_loop/**`, `src/services/order_service/**`,
  `src/services/oms/**`, `src/foundation/**`,
  `src/api/execution_deps.py`, `src/services/background_loops.py`.

**검사 범위(2026-09-06 감사 이전과의 차이)**: 이전 버전은 `__init__`/
`make_*`/`get_*`로만 범위를 좁혀 "이미 만들어진 게이트를 인자로 받아 호출만
하는 일반 함수는 대상이 아니다"라고 가정했다 — 그 가정이 틀렸다.
`is_submission_allowed(pre_submit_gate: PreSubmitGate | None)`처럼 조립부가
`None`을 넘길 수 있게 "게이트를 받아 호출만 하는" 일반 함수 시그니처 자체가
바로 이 검사가 잡아야 할 우회 형태다 — 그 함수 안에서 `if pre_submit_gate
is None: return True`처럼 fail-open으로 이어지기 때문이다. 지금은 클래스
생성자·팩토리를 포함한 **모든 함수**(중첩 함수 포함)의 시그니처를 검사하고,
추가로 **호출 지점에서 게이트 인자에 리터럴 `None`을 넘기는 것**도 잡는다
(시그니처가 나중에 required로 고쳐져도 호출부가 명시적으로 `None`을 박아
넘기면 같은 우회가 재발할 수 있어서다).

이름에 `gate`/`monitor`/`lease`가 포함된 파라미터(`_`로 나눈 토큰 단위 완전
일치, 대소문자 무시, `self`/`cls` 제외)가 다음 중 하나면 위반이다:
- 타입 애너테이션이 `Optional[...]` 또는 `... | None`
- 기본값이 리터럴 `None`
- (호출 지점) 인자로 리터럴 `None`을 전달

토큰 단위로 비교하는 이유: 단순 부분 문자열 비교(`"lease" in name`)는
`release_entry_id`·`aggregate_revision`처럼 안전 게이트와 무관한 이름까지
오탐으로 잡는다(`release`가 `lease`를, `aggregate`가 `gate`를 부분 문자열로
포함). `lease_ttl_seconds`처럼 토큰 자체가 `lease`인 설정값은 여전히
오탐이었는데(안전 게이트/모니터/리스 *객체* 참조가 아니라 TTL 오버라이드
값), `src/services/execution_loop/scheduler.py`에서 `ttl_override_seconds`로
개명해 해소했다 — 파라미터명을 바꾸는 쪽을 택했다(스캐너의 `lease` 마커를
좁히는 대신).

`_scan_violations()`를 다시 돌려 무엇이 걸리는지 항상 최신 상태로 확인할
것 — 이 docstring의 예시가 아니라 실행 결과를 신뢰한다.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_TARGET_GLOBS = (
    "src/services/execution_loop/**/*.py",
    "src/services/order_service/**/*.py",
    "src/services/oms/**/*.py",
    "src/foundation/**/*.py",
)
_TARGET_SINGLE_FILES = (
    "src/api/execution_deps.py",
    "src/services/background_loops.py",
)

_GATE_NAME_MARKERS = ("gate", "monitor", "lease")

_NO_DEFAULT = object()


@dataclass(frozen=True)
class Violation:
    location: str
    function: str
    param: str
    reason: str

    def __str__(self) -> str:
        return f"{self.location}:{self.function}({self.param}) — {self.reason}"


def _matches_gate_marker(name: str) -> bool:
    """`name`을 `_`로 나눈 토큰 중 하나가 게이트 마커와 정확히 일치하는지.

    부분 문자열 비교가 아니라 토큰 단위 비교다 — `release_entry_id`의
    `release`나 `aggregate_revision`의 `aggregate`는 `lease`/`gate`를 부분
    문자열로 포함하지만 토큰으로는 다르므로 걸리지 않는다."""
    tokens = name.lower().split("_")
    return any(marker in tokens for marker in _GATE_NAME_MARKERS)


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
        if not _matches_gate_marker(arg.arg):
            continue
        reason = _annotation_violation_reason(arg.annotation)
        if reason is None and default is not _NO_DEFAULT:
            if isinstance(default, ast.Constant) and default.value is None:
                reason = "기본값 None"
        if reason is not None:
            violations.append(Violation(location, qualname, arg.arg, reason))
    return violations


def _scan_function_defs(tree: ast.AST, location: str) -> list[Violation]:
    """모든 함수(클래스 메서드·중첩 함수 포함)의 시그니처를 검사한다.

    생성자/팩토리로 범위를 좁히지 않는다 — `is_submission_allowed`처럼
    이미 만들어진 게이트를 받아 호출만 하는 일반 함수도 Optional/None
    기본값이면 위반이다(이 검사가 원래 잡으려던 형태)."""
    violations: list[Violation] = []
    method_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    method_ids.add(id(child))
                    violations.extend(_scan_function(child, location, f"{node.name}."))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and id(node) not in method_ids:
            violations.extend(_scan_function(node, location, ""))
    return violations


def _scan_call_sites(tree: ast.AST, location: str) -> list[Violation]:
    """호출 지점에서 게이트 인자에 리터럴 `None`을 넘기는 배선을 잡는다.

    시그니처가 required로 고쳐져도 어떤 호출부가 `pre_submit_gate=None`을
    명시적으로 박아 넘기면(타입체커를 무시하거나 `Any`를 경유해서) 같은
    fail-open 우회가 재발할 수 있다 — 방어 심층화."""
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _dotted_name(node.func) or "<call>"
        for kw in node.keywords:
            if kw.arg is None or not _matches_gate_marker(kw.arg):
                continue
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                violations.append(
                    Violation(location, f"{callee}(...)", kw.arg, "호출 지점에서 리터럴 None 전달")
                )
    return violations


def _scan_source(source: str, location: str) -> list[Violation]:
    tree = ast.parse(source)
    return _scan_function_defs(tree, location) + _scan_call_sites(tree, location)


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
    """negative test — 안전 게이트 인자가 필수(기본값 없음, Optional 아님)면
    위반이 아니다."""
    source = (
        "class Scheduler:\n"
        "    def __init__(self, pre_submit_gate: str) -> None:\n"
        "        pass\n"
    )
    assert _scan_source(source, "fixture.py") == []


def test_scanner_ignores_substring_false_positives():
    """negative test — `release`/`aggregate`처럼 `lease`/`gate`를 부분
    문자열로 포함할 뿐인 이름은 토큰 단위 비교라 걸리지 않는다."""
    source = (
        "def create_batch(release_entry_id: int | None = None) -> None:\n"
        "    pass\n\n"
        "def append_event(aggregate_revision: int | None = None) -> None:\n"
        "    pass\n"
    )
    assert _scan_source(source, "fixture.py") == []


def test_scanner_flags_plain_function_not_just_constructor_or_factory():
    """이 검사가 잡아야 할 바로 그 형태 — 이미 만들어진 게이트를 인자로
    받아 호출만 하는 일반 함수(생성자도 `make_*`/`get_*` 팩토리도 아님)가
    안전 게이트 인자를 Optional로 받으면 위반이다. 이전 버전은 이런 함수를
    "게이트가 없으면 조립부 잘못이지 이 함수 잘못이 아니다"로 보고 검사
    대상에서 뺐지만, 그 함수 내부의 `if pre_submit_gate is None: return
    True`가 바로 fail-open 우회이므로 대상에서 뺄 근거가 없다."""
    source = (
        "async def is_submission_allowed(\n"
        "    pre_submit_gate: str | None,\n"
        "    *, user_id, execution_id,\n"
        ") -> bool:\n"
        "    if pre_submit_gate is None:\n"
        "        return True\n"
    )
    violations = _scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:is_submission_allowed(pre_submit_gate) — `... | None` 애너테이션"
    ]


def test_scanner_ignores_plain_function_with_required_non_optional_gate_param():
    """negative test — 일반 함수라도 게이트 인자가 필수·non-Optional이면
    (이미 만들어진 게이트를 그대로 넘겨받아 호출만 하는 정상적인 형태)
    위반이 아니다."""
    source = (
        "async def is_submission_allowed(pre_submit_gate: str, *, user_id) -> bool:\n"
        "    pass\n"
    )
    assert _scan_source(source, "fixture.py") == []


def test_scanner_flags_call_site_literal_none_for_gate_param():
    """호출 지점에서 `pre_submit_gate=None`을 명시적으로 넘기는 배선도
    잡는다 — 시그니처가 required로 고쳐진 뒤에도 재발 가능한 우회다."""
    source = "submit_order(order, user_id=user_id, pre_submit_gate=None)\n"
    violations = _scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:submit_order(...)(pre_submit_gate) — 호출 지점에서 리터럴 None 전달"
    ]


def test_scanner_ignores_call_site_passing_real_gate_object():
    """negative test — 호출 지점에서 실제 게이트 객체를 넘기면 위반이 아니다."""
    source = "submit_order(order, user_id=user_id, pre_submit_gate=pre_submit_gate)\n"
    assert _scan_source(source, "fixture.py") == []


# --- 회귀 방지 증명: task-1715(P0-B) 수정 전/후 형태 --------------------------


def test_regression_flags_p0b_bypass_shape_before_fix():
    """task-1715(P0-B) 수정 전 `is_submission_allowed`의 실제 시그니처 형태
    (`src/services/execution_loop/pre_submit_check.py`) — 이 검사가 강화되지
    않았다면 놓쳤을 우회를 지금은 잡는다는 증거."""
    source = (
        "async def is_submission_allowed(\n"
        "    pre_submit_gate: object | None,\n"
        "    *, user_id, execution_id, exchange,\n"
        "    mandate_revision_id: object | None = None,\n"
        "    observed_fence: object | None = None,\n"
        ") -> bool:\n"
        "    if pre_submit_gate is None:\n"
        "        return True\n"
    )
    violations = _scan_source(source, "fixture.py")
    assert [v.param for v in violations] == ["pre_submit_gate"]


def test_regression_clears_after_p0b_required_gate_fix():
    """task-1715(P0-B)가 Optional을 제거하고 게이트를 필수 인자로 바꾼 뒤의
    형태 — 위반 0건이어야 하드 게이트가 통과한다(strict 전환의 목표 상태)."""
    source = (
        "async def is_submission_allowed(\n"
        "    pre_submit_gate: object,\n"
        "    *, user_id, execution_id, exchange,\n"
        "    mandate_revision_id: object | None,\n"
        "    observed_fence: object | None,\n"
        ") -> bool:\n"
        "    ...\n"
    )
    assert _scan_source(source, "fixture.py") == []


# --- 실제 배선 코드 검사 -----------------------------------------------------


def test_no_optional_safety_gate_constructor_params():
    """하드 게이트 — xfail 없음. 위반이 있으면 CI가 빨간불이 된다.

    2026-09-06 기준 알려진 잔여 위반: task-1715(P0-B)가 아직 해소하지 않은
    `pre_submit_check.py`/`tick.py`/`submit.py`의 실제 fail-open 배선.
    1715가 게이트 인자를 필수로 바꾸면 여기가 통과로 전환된다 — 위
    `test_regression_*` 두 테스트가 그 전/후 형태 각각에서 스캐너가 정확히
    반응함을 이미 고정 fixture로 증명해 두었으므로, 이 테스트가 지금
    실패하는 것은 스캐너 결함이 아니라 아직 병합되지 않은 P0-B 자체를
    가리키는 정확한 신호다."""
    violations = _scan_violations()
    assert violations == [], "\n".join(str(v) for v in violations)
