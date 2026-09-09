"""CM-8 정적 검사 — CM-A1("컴플라이언스 판정 없이 주문이 제출되는 경로는
존재하지 않는다", I-10) + `submit_order` 시그니처 확장(risk_decision_id/
compliance_decision_id 둘 다 필수) 우회 형태를 잡는다.

`scripts/check_zone_manifest.py`/`tests/unit/test_gate_params_required.py`
(EO-06 I-01)와 같은 AST 정적 스캐너 패턴 — CI에서 `python scripts/
check_compliance_gate.py`로 실행(종료코드 0=통과), pytest로도 그대로
수집된다(같은 함수가 `test_*` 이름이라 이 파일 자체가 테스트다).

두 가지를 검사한다:
1. `src/services/oms/application/submit_order.py`의 `submit_order()`가
   `pre_submit_gate` 호출 결과에서 `decision_id`/`compliance_decision_id`
   둘 다 `None`인지 검사하는 코드를 실제로 갖고 있는가(AST에서 두 attribute
   접근이 같은 함수 안에 존재하는지 확인 — grep보다 리팩터링에 조금 더
   강하다).
2. `src/`(테스트 제외) 전체에서 `PreSubmitGate`/`pre_submit_gate=`에
   `make_foundation_pre_submit_gate` 이외의 값을 대입하는 곳이 있으면 —
   그 값이 무엇이든(다른 팩토리·람다) 사람이 확인해야 하므로 실패시킨다.
   `make_foundation_pre_submit_gate` 자신은 예외(자기 자신을 정의하는 파일).
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUBMIT_ORDER_FILE = "src/services/oms/application/submit_order.py"
_GATE_FACTORY_NAME = "make_foundation_pre_submit_gate"
_GATE_FACTORY_FILE = "src/services/order_service/foundation_gate.py"


def _find_function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def _attribute_reads(node: ast.AST, attr: str) -> list[ast.Attribute]:
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and child.attr == attr
    ]


def _submit_order_checks_both_ids(source: str) -> bool:
    """`submit_order()` 함수 본문 안에 `decision_id`와
    `compliance_decision_id` 두 attribute 읽기가 모두 존재하는지 본다 —
    둘 중 하나라도 지워지면(리팩터링 실수) 이 검사가 실패한다."""
    tree = ast.parse(source)
    func = _find_function(tree, "submit_order")
    if func is None:
        return False
    has_decision_id = bool(_attribute_reads(func, "decision_id"))
    has_compliance_id = bool(_attribute_reads(func, "compliance_decision_id"))
    return has_decision_id and has_compliance_id


def _dotted_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _find_ad_hoc_gate_assignments(source: str, location: str) -> list[str]:
    """`pre_submit_gate=<call>`/`pre_start_gate=<call>`/`pre_send_gate=<call>`
    호출 인자 중, 호출되는 이름이 `make_foundation_pre_submit_gate`도 아니고
    (이미 알려진 위임 함수인) `make_recovery_gate`도 아니면 보고한다 — 새
    팩토리가 생겼다면 이 목록에 추가하고 왜 CM-8 컴플라이언스 게이트를
    우회하지 않는지 확인해야 한다."""
    known_delegates = {_GATE_FACTORY_NAME, "make_recovery_gate"}
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in ("pre_submit_gate", "pre_start_gate", "pre_send_gate"):
                continue
            value = kw.value
            if isinstance(value, ast.Constant) and value.value is None:
                violations.append(f"{location}: {kw.arg}=None 리터럴 대입")
                continue
            if isinstance(value, ast.Call):
                callee = _dotted_name(value.func)
                short = callee.rsplit(".", 1)[-1] if callee else None
                if short not in known_delegates:
                    violations.append(
                        f"{location}: {kw.arg}={callee}(...) — 알 수 없는 게이트 팩토리"
                    )
    return violations


def _iter_production_files() -> list[Path]:
    return sorted(
        p
        for p in (_REPO_ROOT / "src").rglob("*.py")
        if p.is_file() and "__pycache__" not in p.parts
    )


def test_submit_order_checks_both_decision_ids() -> None:
    """CM-A1 — `submit_order()`가 risk/compliance decision id 둘 다 확인하는
    코드를 잃지 않았는지(정적 회귀 방지)."""
    source = (_REPO_ROOT / _SUBMIT_ORDER_FILE).read_text(encoding="utf-8")
    assert _submit_order_checks_both_ids(source), (
        f"{_SUBMIT_ORDER_FILE}: submit_order()가 decision_id/compliance_decision_id "
        "둘 다 확인하지 않습니다 — CM-A1 우회."
    )


def test_no_ad_hoc_pre_submit_gate_factories_in_production() -> None:
    """CM-A1 — production 코드(`src/`)의 어떤 조립부도
    `make_foundation_pre_submit_gate`(CM-8 컴플라이언스 판정을 포함하는
    유일한 실제 구현체) 이외의 값을 게이트로 대입하지 않는다."""
    violations: list[str] = []
    for path in _iter_production_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel == _GATE_FACTORY_FILE:
            continue  # 정의하는 파일 자신은 대상이 아니다
        violations.extend(_find_ad_hoc_gate_assignments(path.read_text(encoding="utf-8"), rel))
    assert violations == [], "\n".join(violations)


def main() -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        test_submit_order_checks_both_decision_ids()
        test_no_ad_hoc_pre_submit_gate_factories_in_production()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    print("OK: CM-8 compliance gate 정적 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
