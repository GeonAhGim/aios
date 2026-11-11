"""scripts/check_format.py -- tests/ 포맷 게이트."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_format import check, main


def test_passes_on_a_formatted_file(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    code, _ = check([str(f)])
    assert code == 0


def test_fails_on_an_unformatted_file_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """적색 재현 -- 어긋난 파일이 있으면 실제로 실패하고 파일 이름을 보여 준다."""
    f = tmp_path / "bad.py"
    f.write_text("x   =   1\n", encoding="utf-8")
    assert main([str(f)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "bad.py" in out


def test_default_scope_is_tests_and_currently_clean() -> None:
    """정리 뒤 tests/ 전체가 통과해야 한다 -- 이 테스트가 게이트의 현재 상태를 고정한다."""
    code, out = check([])
    assert code == 0, out[-800:]
