"""FA-5 DoD(2) — `scripts/check_entity_context.py` 스캐너 정확성 + 실제
배선 코드 위반 0건 단언.

`tests/unit/scripts/test_check_type_ignore_budget.py`와 같은 관례로
`importlib.util`을 통해 스크립트를 모듈로 로드한다(scripts/는 패키지가
아니다).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_entity_context = _load_module(
    "check_entity_context", SCRIPTS_DIR / "check_entity_context.py"
)


def test_scanner_flags_write_call_without_context_param():
    source = (
        "async def submit_order(cmd, *, pool):\n"
        "    await conn.execute(SQL, cmd.order_id)\n"
    )
    violations = check_entity_context._scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:submit_order — entity_context 없이 저장소 write를 호출합니다"
    ]


def test_scanner_ignores_write_call_with_context_param():
    """negative — `entity_context`(또는 이름에 `context` 토큰이 들어간
    파라미터)가 있으면 같은 write 호출도 위반이 아니다."""
    source = (
        "async def submit_order(cmd, *, pool, entity_context):\n"
        "    await conn.execute(SQL, cmd.order_id, entity_context.fund_id)\n"
    )
    assert check_entity_context._scan_source(source, "fixture.py") == []


def test_scanner_ignores_functions_without_write_calls():
    """negative — write로 분류되는 메서드 호출이 전혀 없는 함수(조회 전용
    등)는 entity_context가 없어도 위반이 아니다."""
    source = (
        "async def get_order(order_id, *, pool):\n"
        "    return await pool.fetchrow('SELECT * FROM orders WHERE order_id = $1', order_id)\n"
    )
    assert check_entity_context._scan_source(source, "fixture.py") == []


def test_no_missing_entity_context_in_scanned_write_entrypoints():
    """하드 게이트 — FA-5가 실제로 배선한 진입점(submit_order.py)은 지금
    위반 0건이어야 한다. 스캔 대상 범위는 스크립트 docstring 참고(다른
    쓰기 진입점은 각자의 후속 리프로 분리됨, baseline 아님)."""
    violations = check_entity_context.scan_violations()
    assert violations == [], "\n".join(str(v) for v in violations)
