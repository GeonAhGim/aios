"""DEEPEN -- negative / failure-injection / performance 보강 (task-10177).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2)) -- negative test 0건이던
tests/unit/scripts/__init__.py를 DEEPEN 기준에 맞춰 보강한다.

대상: scripts/check_position_key_central.py -- FA-0d 정적 검사(AST 스캐너).
`position_key`를 f-string/문자열 결합/`.join()`으로 직접 조립하는 코드를
잡아낸다(CLAUDE.md §3: position_key는 PositionKey/.parse()로만 생성). 이
스크립트는 기존 테스트 파일이 없어(scripts/*.py 중 test_*.py 미존재 목록)
DEEPEN 대상으로 적합하다.

DoD:
- negative test 3건 이상 (불변식 위반 입력을 명시적으로 거부)
- 실패주입 케이스 1건 이상 (monkeypatch로 의존성 예외 유발)
- `pytest tests/unit/scripts/__init__.py -q` 통과
- docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._perf.relative_budget import RelativeBudget

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cpkc = _load_module("check_position_key_central", _SCRIPTS_DIR / "check_position_key_central.py")


# ── negative tests: direct-assembly patterns must be flagged ──────────


def test_fstring_assignment_to_position_key_is_flagged() -> None:
    """Negative: assigning an f-string directly to `position_key` bypasses
    the central constructor and must be rejected (CLAUDE.md §3)."""
    source = 'position_key = f"{portfolio_id}:{symbol}"\n'
    violations = cpkc._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "f-string" in violations[0].reason


def test_string_concat_assignment_to_position_key_is_flagged() -> None:
    """Negative: `+` string concatenation into `position_key` must be
    rejected -- only PositionKey/.parse() may produce the value."""
    source = 'position_key = portfolio_id + ":" + symbol\n'
    violations = cpkc._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "결합" in violations[0].reason


def test_join_assignment_to_position_key_is_flagged() -> None:
    """Negative: `"...".join(...)` assembling `position_key` must be
    rejected as a direct-construction bypass."""
    source = 'position_key = ":".join([portfolio_id, symbol])\n'
    violations = cpkc._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "join" in violations[0].reason


def test_fstring_keyword_argument_is_flagged() -> None:
    """Negative: passing an assembled f-string as the `position_key=`
    keyword argument of some call must be rejected too, not only plain
    assignment -- the scanner checks call keywords separately."""
    source = 'open_position(position_key=f"{portfolio_id}:{symbol}", qty=1)\n'
    violations = cpkc._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "f-string" in violations[0].reason


def test_modulo_string_formatting_assignment_is_flagged() -> None:
    """Negative: `%`-style string formatting into `position_key` is also a
    direct-assembly bypass (BinOp with Mod), not just `+`."""
    source = 'position_key = "%s:%s" % (portfolio_id, symbol)\n'
    violations = cpkc._scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "결합" in violations[0].reason


# ── positive controls: legitimate central-constructor usage passes ────


def test_position_key_constructor_call_is_not_flagged() -> None:
    """Positive control: assigning the result of `PositionKey(...)` (the
    legal central constructor) must not be flagged."""
    source = "position_key = PositionKey(portfolio_id, symbol)\n"
    assert cpkc._scan_source(source, "fixture.py") == []


def test_position_key_passthrough_is_not_flagged() -> None:
    """Positive control: passing an existing `position_key` value through
    (attribute/name reference, no assembly) must not be flagged."""
    source = "position_key = existing.position_key\n"
    assert cpkc._scan_source(source, "fixture.py") == []


def test_migrations_directory_is_exempt() -> None:
    """Positive control: `src/db/migrations/` is exempt (one-off historical
    backfills predate the domain object)."""
    rel_parts = ("src", "db", "migrations", "versions", "0001_fixture.py")
    assert cpkc._is_exempt(rel_parts) is True


def test_central_constructor_file_itself_is_exempt() -> None:
    """Positive control: `domain/position_key.py` (the constructor's own
    definition) is exempt from scanning itself."""
    rel_parts = ("src", "foundation", "positions", "domain", "position_key.py")
    assert cpkc._is_exempt(rel_parts) is True


# ── failure-injection test ─────────────────────────────────────────────


def test_scan_violations_propagates_on_unreadable_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failure-injection: if reading a scanned file raises (e.g. disk I/O
    error, permission denied), `scan_violations` must propagate the
    exception rather than silently skipping the file -- a swallowed read
    failure would let an unscanned violation through (fail-closed, §3)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "bad.py").write_text('position_key = f"{x}"\n', encoding="utf-8")

    monkeypatch.setattr(cpkc, "_REPO_ROOT", tmp_path)

    def _boom(self: Path, encoding: str = "utf-8") -> str:
        raise OSError("simulated disk read failure")

    monkeypatch.setattr(Path, "read_text", _boom)

    with pytest.raises(OSError, match="simulated disk read failure"):
        cpkc.scan_violations()


def test_scan_source_propagates_on_syntax_error() -> None:
    """Failure-injection: malformed Python source must raise `SyntaxError`
    from `ast.parse` rather than being treated as zero violations -- a
    swallowed parse error would silently pass a file that could not be
    verified (fail-closed)."""
    with pytest.raises(SyntaxError):
        cpkc._scan_source("position_key = f'{x}\n", "broken.py")


# ── numeric performance assertion ──────────────────────────────────────


def test_scan_source_scales_linearly_within_relative_budget() -> None:
    """Numeric performance assertion: scanning 2,000 clean assignment lines
    must stay within a small multiple of a fixed CPU calibration loop --
    this is a single-pass AST walk with O(n) violations, no quadratic
    blowup expected."""
    lines = [f"position_key = PositionKey(portfolio_id, symbol_{i})\n" for i in range(2000)]
    source = "".join(lines)

    budget = RelativeBudget()
    sample = budget.assert_within(
        lambda: cpkc._scan_source(source, "fixture.py"),
        max_ratio=5.0,
        mode="cpu",
        label="check_position_key_central._scan_source/2000 lines",
    )
    assert sample.ratio > 0
