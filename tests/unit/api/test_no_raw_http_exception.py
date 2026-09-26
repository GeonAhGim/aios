"""PLT-21 — `src/api/routers/**`에 남은 raw `HTTPException` 회귀 가드(AST 스캔).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
(row: "test_no_raw_http_exception.py: AST 스캔"), §3.3 근처 규칙: "HTTPException
(status, "문자열")은 신규 코드에서 금지".

`WHITELIST`는 아직 이 규약으로 이관되지 않은 라우터만 담는다 —
- `admin.py`는 이 리프(task-1074)에서 이미 raw HTTPException이 0건이라
  화이트리스트에 없다(있으면 이 파일의 목적 자체가 무의미해진다).
- `foundation/connections.py`·`foundation/mandates.py`·`foundation/
  evidence.py`는 task-1108이, `foundation/paper_control.py`·`foundation/
  performance.py`·`foundation/reconciliation.py`는 task-1217이,
  `foundation/risk_gate.py`·`foundation/trust.py`·`foundation/validation.py`는
  task-1218이 raw HTTPException을 전부 제거해 화이트리스트에서 뺐다.
- `metrics.py`는 애초에 spec §9 PLT-17~21의 "레거시 라우터 15개" 목록 밖
  (PLT-09가 만든 fail-closed 토큰 체크)이라 이 이관 시리즈의 스콥이 아니다
  — 영구 화이트리스트.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROUTERS_ROOT = Path(__file__).resolve().parents[3] / "src" / "api" / "routers"

WHITELIST = {
    "metrics.py",
}


def _raw_http_exception_call_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "HTTPException"
    )


def _router_files() -> list[Path]:
    return sorted(p for p in ROUTERS_ROOT.rglob("*.py") if p.name != "__init__.py")


def test_admin_router_has_zero_raw_http_exception():
    path = ROUTERS_ROOT / "admin.py"
    assert _raw_http_exception_call_count(path.read_text(encoding="utf-8")) == 0


def test_no_raw_http_exception_outside_whitelist():
    violations: dict[str, int] = {}
    for path in _router_files():
        rel = path.relative_to(ROUTERS_ROOT).as_posix()
        if rel in WHITELIST:
            continue
        count = _raw_http_exception_call_count(path.read_text(encoding="utf-8"))
        if count:
            violations[rel] = count
    assert violations == {}


def test_whitelist_entries_still_exist():
    """화이트리스트 항목이 파일 삭제/리네임으로 조용히 죽은 채 남지 않게
    막는다 — 사라진 항목은 화이트리스트에서도 지워야 한다."""
    missing = [rel for rel in WHITELIST if not (ROUTERS_ROOT / rel).exists()]
    assert missing == []


def test_call_count_detects_single_raw_http_exception():
    """불변식 위반: 라우터 함수 안에 raw HTTPException 호출 1건 —
    스캐너는 이를 명시적으로 거부(count>=1)해야 한다."""
    source = (
        "from fastapi import HTTPException\n\n"
        "def handler():\n"
        "    raise HTTPException(404, 'not found')\n"
    )
    assert _raw_http_exception_call_count(source) == 1


def test_call_count_detects_multiple_raw_http_exception_across_functions():
    """불변식 위반: 서로 다른 함수 두 곳에 raw HTTPException 호출 —
    AST 스캔이 함수 스코프 경계를 넘어 전부 집계해야 한다(부분 누락 거부)."""
    source = (
        "from fastapi import HTTPException\n\n"
        "def handler_a():\n"
        "    raise HTTPException(status_code=400, detail='bad request')\n\n"
        "def handler_b():\n"
        "    raise HTTPException(status_code=403, detail='forbidden')\n"
    )
    assert _raw_http_exception_call_count(source) == 2


def test_call_count_detects_raw_http_exception_nested_in_try_except():
    """불변식 위반: try/except 블록 안에 중첩된 raw HTTPException 호출 —
    최상위 문(statement)만 훑는 얕은 스캔이면 놓치는 위치라 별도로 거부해야 한다."""
    source = (
        "from fastapi import HTTPException\n\n"
        "def handler():\n"
        "    try:\n"
        "        do_work()\n"
        "    except ValueError:\n"
        "        raise HTTPException(status_code=422, detail='invalid')\n"
    )
    assert _raw_http_exception_call_count(source) == 1


def test_whitelist_gate_fails_closed_when_entry_missing(monkeypatch, tmp_path):
    """실패주입: 화이트리스트 항목(metrics.py)이 파일 삭제/리네임으로 사라진
    상태를 흉내낸다 — 게이트는 조용히 통과하지 말고 AssertionError로
    fail-closed 해야 한다."""
    empty_root = tmp_path / "routers"
    empty_root.mkdir()
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "ROUTERS_ROOT", empty_root)

    with pytest.raises(AssertionError):
        test_whitelist_entries_still_exist()
