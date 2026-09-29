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


check_entity_context = _load_module("check_entity_context", SCRIPTS_DIR / "check_entity_context.py")


def test_scanner_flags_write_call_without_context_param():
    source = "async def submit_order(cmd, *, pool):\n    await conn.execute(SQL, cmd.order_id)\n"
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


def test_scanner_flags_nested_function_without_context():
    """negative — 중첩 함수(outer가 entity_context를 갖고 있어도) 자신의
    스코프에 write 호출이 있고 자신의 파라미터에 context 토큰이 없으면
    위반이다(주석 §"중첩 함수 자신의 write는 그 함수 스코프에서 별도 검사")."""
    source = (
        "async def outer(cmd, *, pool, entity_context):\n"
        "    async def inner(row):\n"
        "        await conn.execute(SQL, row)\n"
        "    await inner(cmd)\n"
    )
    violations = check_entity_context._scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:inner — entity_context 없이 저장소 write를 호출합니다"
    ]


def test_scanner_ignores_bare_call_not_attribute_access():
    """negative — write 마커는 `obj.execute(...)` 형태의 속성 호출만
    잡는다. 이름이 같은 지역 함수를 바로 호출하는 `execute(...)`(속성 접근이
    아닌 Name 호출)는 저장소 write로 오인해 위반 처리하면 안 된다."""
    source = "async def helper(cmd, *, pool):\n    execute(cmd)\n"
    assert check_entity_context._scan_source(source, "fixture.py") == []


def test_scanner_flags_each_violating_function_independently():
    """negative — 위반 함수가 둘이면 각각 독립적으로 보고돼야 한다(하나만
    잡고 나머지를 누락하면 안 된다)."""
    source = (
        "async def submit_a(cmd, *, pool):\n"
        "    await conn.execute(SQL, cmd.id)\n\n"
        "async def submit_b(cmd, *, pool):\n"
        "    await store.append(cmd.id)\n"
    )
    violations = check_entity_context._scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == [
        "fixture.py:submit_a — entity_context 없이 저장소 write를 호출합니다",
        "fixture.py:submit_b — entity_context 없이 저장소 write를 호출합니다",
    ]


def test_main_returns_1_and_prints_violations(monkeypatch, capsys):
    """실패주입 — `scan_violations`이 위반을 반환하도록 monkeypatch해
    `main()`이 비정상 종료 코드(1)와 각 위반 라인을 표준출력에 내는지
    확인한다(fail-closed: 위반이 있으면 조용히 통과시키지 않는다)."""
    fake_violation = check_entity_context.Violation(
        "fixture.py", "submit_order", "entity_context 없이 저장소 write를 호출합니다"
    )
    monkeypatch.setattr(check_entity_context, "scan_violations", lambda: [fake_violation])
    exit_code = check_entity_context.main()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert str(fake_violation) in out
    assert "1건 위반" in out


def test_scan_violations_raises_when_target_file_missing(monkeypatch):
    """실패주입 — 스캔 대상 파일이 존재하지 않으면 `scan_violations`은
    조용히 빈 리스트를 반환하는 대신 예외를 전파해야 한다(fail-closed:
    스캔 실패를 "위반 0건"으로 위장하면 안 된다)."""
    monkeypatch.setattr(check_entity_context, "_TARGET_FILES", ("src/does/not/exist.py",))
    try:
        check_entity_context.scan_violations()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for missing target file")


def test_no_missing_entity_context_in_scanned_write_entrypoints():
    """하드 게이트 — FA-5가 실제로 배선한 진입점(submit_order.py)은 지금
    위반 0건이어야 한다. 스캔 대상 범위는 스크립트 docstring 참고(다른
    쓰기 진입점은 각자의 후속 리프로 분리됨, baseline 아님)."""
    violations = check_entity_context.scan_violations()
    assert violations == [], "\n".join(str(v) for v in violations)
