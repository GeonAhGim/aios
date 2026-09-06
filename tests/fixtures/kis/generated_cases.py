"""BR-13(task-1930) 계약 테스트 픽스처 -- BR-12(task-1929) 생성 KIS mixin의
TR 케이스 목록.

`docs/design/kis_tr_reference.json`(BR-11, 공식 저장소
`koreainvestment/open-trading-api` 기계 추출본)을 유일한 진실 소스로 삼는다.
`src/exchanges/kis/generated/*.py`(BR-12가 그대로 생성, 손으로 수정 금지)의
소스 AST에서 실제로 조립하는 (tr_id, path, http 메서드) 또는 (WS tr_id)를
직접 뽑아낸다 -- 생성기(`scripts/kis_generate_adapters.py`)의 청크/네이밍
로직을 재구현하지 않고도, "생성된 코드가 실제로 무엇을 조립하는지"를
소스에서 그대로 확인할 수 있다(결정론, I/O는 파일 읽기뿐).

핸드라이팅 mixin(trading_mixin.py 등)은 이미 자체 단위/통합 테스트가
있어(tests/unit/exchanges/kis/, tests/integration/test_kis_*.py) 범위에서
제외한다 -- 이 픽스처는 `generated/`만 다룬다.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REFERENCE_PATH = ROOT / "docs" / "design" / "kis_tr_reference.json"
GENERATED_DIR = ROOT / "src" / "exchanges" / "kis" / "generated"
GENERATED_PACKAGE = "src.exchanges.kis.generated"


class GeneratedCaseDiscoveryError(ValueError):
    """생성 mixin 소스가 렌더러(`kis_generate_adapters.render_method`) 템플릿과
    어긋나 케이스를 뽑아낼 수 없을 때(형태 변경을 감지하지 못하고 조용히
    스킵하는 사고를 막기 위해 fail-closed)."""


@dataclass(frozen=True)
class GeneratedCase:
    tr_id: str
    module_name: str
    class_name: str
    method_name: str
    kind: str  # "rest" | "ws"
    http_method: str | None  # "GET" | "POST"; WS는 None
    path: str | None  # WS는 None
    arg_style: str | None  # "params" | "body"; WS는 None


def load_reference_rows() -> dict[str, dict[str, Any]]:
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    rows = {row["tr_id"]: row for row in data["trs"]}
    if len(rows) != len(data["trs"]):
        raise GeneratedCaseDiscoveryError("kis_tr_reference.json: tr_id 중복 -- 유일성 가정 위반")
    return rows


def _string_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _find_call(node: ast.AST, func_name: str) -> ast.Call | None:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == func_name:
            return child
        if isinstance(func, ast.Name) and func.id == func_name:
            return child
    return None


def _extract_rest(call: ast.Call, qualname: str) -> tuple[str, str, str, str]:
    if len(call.args) < 3 or len(call.keywords) < 1:
        raise GeneratedCaseDiscoveryError(f"{qualname}: self._request() 인자 형태 예상과 다름")
    http_method = _string_const(call.args[0])
    path = _string_const(call.args[1])
    tr_id = _string_const(call.args[2])
    arg_style = call.keywords[0].arg
    if not (http_method and path and tr_id and arg_style in ("params", "body")):
        raise GeneratedCaseDiscoveryError(f"{qualname}: self._request() 인자를 상수로 못 뽑음")
    return http_method, path, tr_id, arg_style


def _extract_ws(call: ast.Call, qualname: str) -> str:
    if len(call.args) < 2:
        raise GeneratedCaseDiscoveryError(f"{qualname}: _build_subscribe_message() 인자 부족")
    tr_id = _string_const(call.args[1])
    if tr_id is None:
        raise GeneratedCaseDiscoveryError(f"{qualname}: WS tr_id를 상수로 못 뽑음")
    return tr_id


def discover_generated_cases() -> list[GeneratedCase]:
    """`generated/*_mixin.py`(청크 파일)를 전부 순회해 각 메서드가 실제로
    조립하는 요청/구독 정보를 뽑는다. 렌더러 템플릿이 바뀌어 더 이상 파싱할
    수 없으면(GeneratedCaseDiscoveryError) 조용히 빈 목록을 내지 않고 즉시
    fail-closed 한다 -- 그래야 "계약 테스트가 사실 아무 것도 못 잡고
    있었다"는 조용한 회귀를 막는다."""
    cases: list[GeneratedCase] = []
    for path in sorted(GENERATED_DIR.glob("*_mixin.py")):
        module_name = f"{GENERATED_PACKAGE}.{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        class_defs = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        if len(class_defs) != 1:
            raise GeneratedCaseDiscoveryError(f"{path}: mixin 클래스가 정확히 1개가 아님")
        class_node = class_defs[0]
        for func in class_node.body:
            if not isinstance(func, ast.AsyncFunctionDef):
                continue
            qualname = f"{module_name}.{class_node.name}.{func.name}"
            request_call = _find_call(func, "_request")
            if request_call is not None:
                http_method, req_path, tr_id, arg_style = _extract_rest(request_call, qualname)
                cases.append(
                    GeneratedCase(
                        tr_id=tr_id,
                        module_name=module_name,
                        class_name=class_node.name,
                        method_name=func.name,
                        kind="rest",
                        http_method=http_method,
                        path=req_path,
                        arg_style=arg_style,
                    )
                )
                continue
            subscribe_call = _find_call(func, "_build_subscribe_message")
            if subscribe_call is None:
                raise GeneratedCaseDiscoveryError(f"{qualname}: 알려진 생성 메서드 형태가 아님")
            tr_id = _extract_ws(subscribe_call, qualname)
            cases.append(
                GeneratedCase(
                    tr_id=tr_id,
                    module_name=module_name,
                    class_name=class_node.name,
                    method_name=func.name,
                    kind="ws",
                    http_method=None,
                    path=None,
                    arg_style=None,
                )
            )
    return cases
