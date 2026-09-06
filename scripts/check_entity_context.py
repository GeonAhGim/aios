"""FA-5 정적 검사 — "쓰기 유스케이스가 entity_context 없이 저장소 write를
호출"하는 형태를 AST로 잡는다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5
(§9 FA-5 DoD "컨텍스트 없는 쓰기 정적 검사 0건"), `tests/unit/
test_gate_params_required.py`(I-01/EO-06)와 같은 형태의 AST 스캐너를
"게이트 인자 Optional 검사"에서 "컨텍스트 인자 존재 검사"로 바꾼 것이다.

**스캔 대상(2026-09-06 FA-5 leaf 기준)**: 이 리프가 실제로 `entity_context`
필수 인자를 배선한 진입점만 — `src/services/oms/application/submit_order.py`.
`src/foundation/positions/application/record_fill.py`·`src/foundation/
ledger/application/post_entry.py`는 결정(decision) §2가 "3개 쓰기 진입점"
으로 명명한 나머지 둘이지만, 각각 프로덕션 호출부가 4곳·9곳(챠지백·환불·
정산·구매·충전 흐름 등)에 걸쳐 있어 이 리프 하나로 안전하게 재배선할 수
없다(호출부 전체 재검증 없이 시그니처만 바꾸면 기존 흐름이 즉시 깨진다).
범위 밖 캡처(후속 리프 대상):
  - `src/foundation/positions/application/record_fill.py`
    (호출부: `src/services/order_service/submit.py`,
    `src/services/order_service/fenced_submit.py`,
    `src/services/oms/application/inbox_processor.py`,
    `src/services/order_service/position_ledger.py`)
  - `src/foundation/ledger/application/post_entry.py`
    (호출부: `chargeback.py`·`refund.py`·`payouts.py`·`purchase_flow.py`·
    `topup.py`·`backfill.py`·`legacy_wallet_bridge.py`·
    `dispute_resolution_service.py`·`purchase_service.py`)
이 스크립트는 "baseline 허용"이 아니다 — 위 목록은 이 스크립트가 아직
스캔하지 않는 파일이라 위반으로 잡히지 않을 뿐이고, 스캔 대상에 넣는 순간
그 리프가 시그니처+호출부를 함께 고쳐야 한다(xfail로 얼버무리지 않는다).

탐지 규칙: 스캔 대상 파일의 각 함수(중첩 포함) 본문에서 "저장소 write로
보이는 호출"(`_WRITE_CALL_MARKERS`의 메서드명 — `execute`/`append`/
`transition`/`claim`/`enqueue`/`upsert`)이 나타나면, 그 함수 자신의
파라미터 이름 중 하나가 "context" 토큰을 포함해야 한다(대소문자 무시,
`entity_context`가 표준형). 없으면 위반.

사용: `python scripts/check_entity_context.py` (저장소 루트에서). 종료코드
0=위반 없음, 1=위반 있음(각 위반을 표준출력에 나열).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_TARGET_FILES = ("src/services/oms/application/submit_order.py",)

_WRITE_CALL_MARKERS = frozenset(
    {"execute", "append", "transition", "claim", "enqueue", "upsert"}
)
_CONTEXT_MARKER = "context"


@dataclass(frozen=True)
class Violation:
    location: str
    function: str
    reason: str

    def __str__(self) -> str:
        return f"{self.location}:{self.function} — {self.reason}"


def _call_attr_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _has_write_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(func):
        if child is func:
            continue
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            continue  # 중첩 함수 자신의 write는 그 함수 스코프에서 별도 검사
        if isinstance(child, ast.Call) and _call_attr_name(child.func) in _WRITE_CALL_MARKERS:
            return True
    return False


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = func.args
    return [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]


def _has_context_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_CONTEXT_MARKER in name.lower() for name in _param_names(func))


def _scan_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef, location: str
) -> Violation | None:
    if not _has_write_call(func):
        return None
    if _has_context_param(func):
        return None
    return Violation(location, func.name, "entity_context 없이 저장소 write를 호출합니다")


def _scan_source(source: str, location: str) -> list[Violation]:
    tree = ast.parse(source)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            v = _scan_function(node, location)
            if v is not None:
                violations.append(v)
    return violations


def scan_violations() -> list[Violation]:
    violations: list[Violation] = []
    for rel in _TARGET_FILES:
        path = _REPO_ROOT / rel
        violations.extend(_scan_source(path.read_text(encoding="utf-8"), rel))
    return violations


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    violations = scan_violations()
    if violations:
        for v in violations:
            print(str(v))
        print(f"check_entity_context: {len(violations)}건 위반")
        return 1
    print("check_entity_context: 위반 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
