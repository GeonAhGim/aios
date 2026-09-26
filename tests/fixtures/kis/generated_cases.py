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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

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


# ---------------------------------------------------------------------------
# task-7730 DEEPEN(원 리프 task-6704) -- negative test 3건 + 실패주입 1건.
#
# 위 discovery 로직은 "생성 소스가 렌더러 템플릿과 어긋나면 즉시
# fail-closed"를 스스로 약속한다(`GeneratedCaseDiscoveryError` docstring).
# 그런데 이 파일에는 그 약속을 실제로 지키는지 확인하는 테스트가 하나도
# 없었다 -- 렌더러 템플릿을 못 알아채고도 조용히 빈 목록을 내는 회귀가
# 생겨도 아무 테스트도 잡지 못한다. 아래는 `docs/design/kis_tr_reference.json`
# 과 `src/exchanges/kis/generated/*.py`(BR-12 원본, 손으로 수정 금지) 대신
# `tmp_path`에 최소 재현 픽스처를 써서, 이 fail-closed 약속 자체를 직접
# 실증한다.
# ---------------------------------------------------------------------------


def test_load_reference_rows_rejects_duplicate_tr_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative -- 기준 목록 원문에 tr_id가 중복되면(BR-11 소스 오염 또는
    기계 추출 스크립트 결함) 조용히 마지막 행으로 덮어쓰지 않고 즉시
    거부해야 한다(유일성 가정 위반)."""
    reference_path = tmp_path / "kis_tr_reference.json"
    reference_path.write_text(
        json.dumps({"trs": [{"tr_id": "DUPE0001"}, {"tr_id": "DUPE0001"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "REFERENCE_PATH", reference_path)

    with pytest.raises(GeneratedCaseDiscoveryError, match="tr_id 중복"):
        load_reference_rows()


def test_discover_generated_cases_rejects_module_without_exactly_one_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative -- 생성 청크 파일에 mixin 클래스가 0개 또는 2개 이상이면
    "어느 클래스가 진짜 생성 mixin인지" 결정할 수 없으므로, 첫 번째를
    임의로 골라 조용히 계속하지 않고 즉시 거부해야 한다."""
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    (generated_dir / "broken_mixin.py").write_text(
        "class A:\n    pass\n\n\nclass B:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "GENERATED_DIR", generated_dir)

    with pytest.raises(GeneratedCaseDiscoveryError, match="정확히 1개가 아님"):
        discover_generated_cases()


def test_discover_generated_cases_rejects_unknown_method_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative -- async 메서드가 `_request()`도 `_build_subscribe_message()`도
    호출하지 않으면(렌더러 템플릿이 바뀌었거나 손으로 수정된 흔적), 그
    메서드를 조용히 건너뛰어 케이스 수가 소리 없이 줄어드는 회귀 대신
    즉시 거부해야 한다(discover_generated_cases의 fail-closed 계약)."""
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    (generated_dir / "unknown_mixin.py").write_text(
        "class UnknownMixin:\n    async def do_something(self, params=None):\n        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "GENERATED_DIR", generated_dir)

    with pytest.raises(GeneratedCaseDiscoveryError, match="알려진 생성 메서드 형태가 아님"):
        discover_generated_cases()


def test_extract_rest_rejects_insufficient_call_shape() -> None:
    """negative -- `self._request(...)` 호출 인자/키워드가 렌더러가 항상
    보장하는 형태(위치인자 3개 + 키워드 1개)보다 적으면, None을 반환하거나
    IndexError로 죽는 대신 명시적으로 거부해야 한다."""
    call = ast.parse("self._request('GET', '/x')", mode="eval").body
    assert isinstance(call, ast.Call)

    with pytest.raises(GeneratedCaseDiscoveryError, match="인자 형태 예상과 다름"):
        _extract_rest(call, "fixture.qualname")


def test_extract_ws_rejects_non_constant_tr_id() -> None:
    """negative -- `_build_subscribe_message()`의 tr_id 인자가 상수 문자열이
    아니면(예: 변수 참조로 렌더러가 바뀜) 조용히 None/빈 문자열을 tr_id로
    쓰지 않고 즉시 거부해야 한다."""
    call = ast.parse("self._build_subscribe_message(approval_key, some_variable)", mode="eval").body
    assert isinstance(call, ast.Call)

    with pytest.raises(GeneratedCaseDiscoveryError, match="상수로 못 뽑음"):
        _extract_ws(call, "fixture.qualname")


def test_discover_generated_cases_failure_injected_read_text_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입 -- 청크 파일 읽기 자체가 I/O 예외로 죽으면(디스크 장애,
    권한 문제 등) 이를 삼켜 빈 케이스 목록으로 계속 진행하지 않고 그대로
    전파해야 한다(조용한 회귀 대신 fail-closed, 파일 상단 모듈 docstring의
    "I/O는 파일 읽기뿐"은 실패해도 안전해야 한다는 뜻이지 실패를 숨겨도
    된다는 뜻이 아니다)."""
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    (generated_dir / "unreadable_mixin.py").write_text(
        "class UnreadableMixin:\n    pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "GENERATED_DIR", generated_dir)

    original_read_text = Path.read_text

    def _failing_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "unreadable_mixin.py":
            raise OSError("simulated disk failure reading generated chunk")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _failing_read_text)

    with pytest.raises(OSError, match="simulated disk failure"):
        discover_generated_cases()
