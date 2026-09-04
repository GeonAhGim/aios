"""PLT-21 — `src/api/routers/**`에 남은 raw `HTTPException` 회귀 가드(AST 스캔).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
(row: "test_no_raw_http_exception.py: AST 스캔"), §3.3 근처 규칙: "HTTPException
(status, "문자열")은 신규 코드에서 금지".

`WHITELIST`는 아직 이 규약으로 이관되지 않은 라우터만 담는다 —
- `admin.py`는 이 리프(task-1074)에서 이미 raw HTTPException이 0건이라
  화이트리스트에 없다(있으면 이 파일의 목적 자체가 무의미해진다).
- `foundation/connections.py`·`foundation/mandates.py`·`foundation/
  evidence.py`는 task-1108이 raw HTTPException을 전부 제거해 화이트리스트
  에서 뺐다. 나머지 `foundation/*.py` 6개는 후속 직렬 리프(task-1217·1218,
  exception_mapping.py 동시 수정 충돌을 피하려고 task-1108과 depends_on으로
  직렬화됨)가 뺄 때까지 남겨 둔다.
- `metrics.py`는 애초에 spec §9 PLT-17~21의 "레거시 라우터 15개" 목록 밖
  (PLT-09가 만든 fail-closed 토큰 체크)이라 이 이관 시리즈의 스콥이 아니다
  — 영구 화이트리스트.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROUTERS_ROOT = Path(__file__).resolve().parents[3] / "src" / "api" / "routers"

WHITELIST = {
    "metrics.py",
    "foundation/paper_control.py",
    "foundation/performance.py",
    "foundation/reconciliation.py",
    "foundation/risk_gate.py",
    "foundation/trust.py",
    "foundation/validation.py",
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
