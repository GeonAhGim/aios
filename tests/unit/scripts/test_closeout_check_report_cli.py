"""scripts/closeout_check.py 단위 테스트 — task-2738(CLOSEOUT), 리포트/CLI.

`render_markdown`/`write_closeout_doc`/`main`을 다룬다. 종료조건별 검사는
`test_closeout_check.py`(1~9), `test_closeout_check_ops_hardening.py`(10~11),
`test_closeout_check_head_actions.py`(12)에 있다. 공용 로더는
`closeout_check_loader.py`.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests.unit.scripts.closeout_check_loader import ROOT, cc


def test_check_result_is_frozen_and_rejects_mutation() -> None:
    """불변식: CheckResult는 frozen dataclass — 판정 결과를 사후 변조할 수 없다."""
    result = cc.CheckResult("01_x", "제목", True, (), "요약")

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.passed = False


def test_main_rejects_unknown_cli_argument() -> None:
    """불변식: argparse는 정의되지 않은 플래그를 조용히 무시하지 않고 거부한다."""
    with pytest.raises(SystemExit) as exc_info:
        cc.main(["--no-such-flag"])

    assert exc_info.value.code == 2


def test_main_rejects_repo_root_without_value() -> None:
    """불변식: `--repo-root`는 값이 필수 — 값 없이 주면 거부돼야 한다."""
    with pytest.raises(SystemExit) as exc_info:
        cc.main(["--repo-root"])

    assert exc_info.value.code == 2


def test_write_closeout_doc_rejects_when_parent_path_is_a_file(tmp_path) -> None:
    """불변식: 문서 경로의 부모가 이미 평범한 파일이면 디렉터리로 만들 수 없다."""
    results = [cc.CheckResult("01_x", "제목", True, (), "요약")]
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    out = blocker / "CLOSEOUT.md"

    with pytest.raises((NotADirectoryError, FileExistsError)):
        cc.write_closeout_doc(out, results)


def test_main_propagates_when_run_all_raises(tmp_path, monkeypatch) -> None:
    """실패주입: 검사 함수 하나가 예외를 던지면 main은 삼키지 않고 그대로 전파한다(fail-closed)."""

    def _boom(*_a, **_k):
        raise RuntimeError("check crashed")

    monkeypatch.setattr(cc, "run_all", _boom)

    with pytest.raises(RuntimeError, match="check crashed"):
        cc.main(["--repo-root", str(tmp_path)])


def test_render_markdown_contains_table_and_evidence() -> None:
    results = [cc.CheckResult("01_x", "제목", True, ("evidence-a",), "요약")]

    markdown = cc.render_markdown(results)

    assert "| 1 | 제목 | PASS | 요약 |" in markdown
    assert "evidence-a" in markdown


def test_write_closeout_doc_writes_file(tmp_path) -> None:
    results = [cc.CheckResult("01_x", "제목", True, (), "요약")]
    out = tmp_path / "docs" / "milestones" / "MVP-1_CLOSEOUT.md"

    cc.write_closeout_doc(out, results)

    assert out.is_file()
    assert "MVP-1 종료 확인서" in out.read_text(encoding="utf-8")


def test_main_returns_1_and_skips_write_when_any_check_fails(tmp_path, monkeypatch) -> None:
    fake_results = [
        cc.CheckResult("01_x", "제목1", True, (), "ok"),
        cc.CheckResult("02_y", "제목2", False, (), "적색"),
    ]
    monkeypatch.setattr(cc, "run_all", lambda *a, **k: fake_results)
    out = tmp_path / "CLOSEOUT.md"

    exit_code = cc.main(["--repo-root", str(tmp_path), "--write", str(out)])

    assert exit_code == 1
    assert not out.exists()


def test_main_returns_0_and_writes_when_all_checks_pass(tmp_path, monkeypatch) -> None:
    fake_results = [cc.CheckResult("01_x", "제목1", True, (), "ok")]
    monkeypatch.setattr(cc, "run_all", lambda *a, **k: fake_results)
    out = tmp_path / "CLOSEOUT.md"

    exit_code = cc.main(["--repo-root", str(tmp_path), "--write", str(out)])

    assert exit_code == 0
    assert out.is_file()


def test_main_against_real_repo_currently_returns_1() -> None:
    """MVP-1은 아직 종료조건을 전부 채우지 못했다 — 현재 적색 상태를 고정."""
    assert cc.main(["--repo-root", str(ROOT)]) == 1
