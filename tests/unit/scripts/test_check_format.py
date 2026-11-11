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


def test_protected_dirs_come_from_the_zone_manifest() -> None:
    """제외 목록을 손으로 따로 적지 않는다 -- .aios-zone의 FROZEN_PAPER_ONLY와 같아야 한다."""
    from scripts.check_format import protected_dirs

    dirs = protected_dirs()
    assert set(dirs) == {
        "src/core/strategy",
        "src/core/portfolio",
        "src/core/risk",
        "src/core/executor",
    }


def test_protected_dirs_parses_only_the_frozen_paper_only_block(tmp_path: Path) -> None:
    from scripts.check_format import protected_dirs

    zone = tmp_path / ".aios-zone"
    zone.write_text(
        "zones:\n"
        "  FROZEN:\n"
        '    - "aios/kernel/policy/**"\n'
        "  FROZEN_PAPER_ONLY:\n"
        '    - "src/a/**"\n'
        '    - "src/b/**"\n'
        "  SCAFFOLD:\n"
        '    - "src/c/**"\n',
        encoding="utf-8",
    )
    assert protected_dirs(zone) == ["src/a", "src/b"]
    assert protected_dirs(tmp_path / "missing") == []


def test_generated_dirs_exist_and_match_the_generator_contract() -> None:
    """GENERATED_DIRS가 실재해야 한다 -- 사라진 경로를 계속 제외한다고 착각하지 않게."""
    from scripts.check_format import GENERATED_DIRS, ROOT

    for d in GENERATED_DIRS:
        assert (ROOT / d).is_dir(), d
