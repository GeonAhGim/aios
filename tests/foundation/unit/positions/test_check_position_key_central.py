"""FA-0d -- `scripts/check_position_key_central.py` 스캐너 정확성 + 실제
`src/` 위반 0건 단언.

`tests/foundation/unit/entities/test_check_entity_context.py`와 같은
관례로 `importlib.util`을 통해 스크립트를 모듈로 로드한다(scripts/는
패키지가 아니다).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_position_key_central = _load_module(
    "check_position_key_central", SCRIPTS_DIR / "check_position_key_central.py"
)


def test_flags_fstring_assembly():
    source = (
        "def build(venue, instrument_id):\n"
        '    position_key = f"{venue}:{instrument_id}:default:paper"\n'
        "    return position_key\n"
    )
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert [str(v) for v in violations] == ["fixture.py:2 — f-string으로 직접 조립"]


def test_flags_string_concat_assembly():
    source = 'def build(venue, instrument_id):\n    position_key = venue + ":" + instrument_id\n'
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "결합" in violations[0].reason


def test_flags_join_assembly():
    source = 'def build(parts):\n    position_key = ":".join(parts)\n'
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "join" in violations[0].reason


def test_flags_assembly_passed_as_keyword_argument():
    source = (
        "def build(venue, instrument_id):\n"
        "    return RecordFillCommand(\n"
        '        position_key=f"{venue}:{instrument_id}:default:paper",\n'
        "    )\n"
    )
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert len(violations) == 1


def test_ignores_central_constructor_via_str_wrap():
    """negative -- `str(PositionKey(...))`는 중앙 생성자 경유이므로 위반이 아니다."""
    source = (
        "def build(venue, instrument_id, strategy_id, execution_id, portfolio_id):\n"
        "    position_key = str(\n"
        "        PositionKey(\n"
        "            venue=venue, instrument_id=instrument_id, strategy_id=strategy_id,\n"
        "            execution_id=execution_id, portfolio_id=portfolio_id,\n"
        "        )\n"
        "    )\n"
        "    return position_key\n"
    )
    assert check_position_key_central._scan_source(source, "fixture.py") == []


def test_ignores_passthrough_of_existing_value():
    """negative -- 이미 만들어진 값을 그대로 넘기는 것(Name/Attribute)은 위반이 아니다."""
    source = (
        "async def record_fill(conn, command):\n"
        "    position_key = command.position_key\n"
        "    return position_key\n"
    )
    assert check_position_key_central._scan_source(source, "fixture.py") == []


def test_flags_percent_format_assembly():
    """negative -- `%` 결합도 `_is_assembly_expr`의 BinOp(Mod) 분기를 태운다.

    기존 케이스는 `+` 결합만 다뤘다 -- `Add | Mod` 유니온의 두 번째 분기가
    실제로 위반을 거부(탐지)하는지 별도로 확인한다."""
    source = (
        "def build(venue, instrument_id):\n"
        '    position_key = "%s:%s:default:paper" % (venue, instrument_id)\n'
    )
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "결합" in violations[0].reason


def test_flags_annotated_assignment_assembly():
    """negative -- 타입 주석이 붙은 대입(`AnnAssign`)도 조립 위반이면 거부한다.

    `_assign_target_value`는 `Assign`과 `AnnAssign`을 분기 처리하는데 지금까지
    `AnnAssign` 경로는 테스트가 없었다."""
    source = (
        "def build(venue, instrument_id):\n"
        '    position_key: str = f"{venue}:{instrument_id}:default:paper"\n'
        "    return position_key\n"
    )
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "f-string" in violations[0].reason


def test_flags_chained_assignment_assembly():
    """negative -- 체인 대입(`a = position_key = ...`)의 여러 target 중 하나라도
    `position_key`면 위반으로 거부한다(`_assign_violations`의 targets 순회)."""
    source = (
        "def build(venue, instrument_id):\n"
        '    audit_copy = position_key = venue + ":" + instrument_id\n'
        "    return audit_copy, position_key\n"
    )
    violations = check_position_key_central._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "결합" in violations[0].reason


def test_scan_violations_propagates_file_read_failure(monkeypatch, tmp_path):
    """실패주입 -- 소스 파일을 읽는 의존성(`Path.read_text`)이 예외를 던지면
    스캐너는 이를 삼키지 않고 그대로 전파해야 한다(조용한 통과 금지, fail-closed)."""
    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "broken.py").write_text("position_key = 1\n", encoding="utf-8")

    monkeypatch.setattr(check_position_key_central, "_REPO_ROOT", tmp_path)

    failure = OSError("injected read_text failure")

    def _broken_read_text(self, *args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, "read_text", _broken_read_text)

    with pytest.raises(OSError, match="injected read_text failure") as excinfo:
        check_position_key_central.scan_violations()
    assert excinfo.value is failure


def test_no_assembly_violations_in_scanned_src_tree():
    """하드 게이트 -- 실제 `src/`(마이그레이션 제외) 전체는 지금 위반 0건이어야
    한다(task-1943, ADR-2026-09-06-G §1 -- baseline으로 허용하지 않는다)."""
    violations = check_position_key_central.scan_violations()
    assert violations == [], "\n".join(str(v) for v in violations)
