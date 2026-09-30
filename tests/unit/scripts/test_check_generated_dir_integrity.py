"""scripts/check_generated_dir_integrity.py 단위 테스트 -- task-9139.

DoD(D2): negative >=3, 실패주입 1, 성능단언 1, red-gate 재현 1. DB·네트워크 없음 --
임시 디렉터리와 로컬 git 명령(네트워크 없는 `git init`류)만 쓴다.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_generated_dir_integrity = _load_module(
    "check_generated_dir_integrity", SCRIPTS_DIR / "check_generated_dir_integrity.py"
)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo_with_generated_file(repo: Path) -> None:
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "test")
    gen_dir = repo / "src" / "exchanges" / "kis" / "generated"
    gen_dir.mkdir(parents=True)
    (gen_dir / "account_tr_labels.py").write_text(
        'LABELS = {"TTTC0000": "label"}\n', encoding="utf-8"
    )
    other_dir = repo / "src" / "exchanges" / "kis"
    (other_dir / "account_mixin.py").write_text("# handwritten\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "base")
    _run_git(repo, "branch", "base")


# ---------------------------------------------------------------------------
# 양성 경로
# ---------------------------------------------------------------------------


def test_no_deletion_passes(tmp_path: Path) -> None:
    _init_repo_with_generated_file(tmp_path)

    (tmp_path / "src" / "exchanges" / "kis" / "account_mixin.py").write_text(
        "# handwritten, edited\n", encoding="utf-8"
    )
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "edit handwritten file only")

    exit_code = check_generated_dir_integrity.main(
        ["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    assert exit_code == 0


def test_deleting_non_generated_file_passes(tmp_path: Path) -> None:
    """negative 경계: generated/ 밖의 삭제는 이 게이트의 관심사가 아니다."""
    _init_repo_with_generated_file(tmp_path)

    (tmp_path / "src" / "exchanges" / "kis" / "account_mixin.py").unlink()
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "delete handwritten file")

    exit_code = check_generated_dir_integrity.main(
        ["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    assert exit_code == 0


# ---------------------------------------------------------------------------
# negative / red-gate 재현 -- 이 게이트가 실제로 막으려는 회귀 클래스
# (task-8850, task-9055: generated/ 산출물을 "미사용" 판단으로 삭제)
# ---------------------------------------------------------------------------


def test_deleting_generated_file_fails(tmp_path: Path) -> None:
    """red-gate 재현: task-8850/task-9055가 실제로 한 일(generated/*.py 손 삭제)을
    그대로 재현해 이 게이트가 잡아내는지 확인한다."""
    _init_repo_with_generated_file(tmp_path)

    (tmp_path / "src" / "exchanges" / "kis" / "generated" / "account_tr_labels.py").unlink()
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "janitor: remove unused-looking file")

    exit_code = check_generated_dir_integrity.main(
        ["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    assert exit_code == 1


def test_deleting_generated_file_prints_offending_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """negative: 실패 메시지가 삭제된 경로를 실제로 지목해야 한다(무음/모호 실패 금지)."""
    _init_repo_with_generated_file(tmp_path)

    (tmp_path / "src" / "exchanges" / "kis" / "generated" / "account_tr_labels.py").unlink()
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "delete generated file")

    check_generated_dir_integrity.main(
        ["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    captured = capsys.readouterr()
    assert "generated/account_tr_labels.py" in captured.out


def test_nonexistent_base_ref_fails_closed_via_real_git_diff_error(tmp_path: Path) -> None:
    """negative: 존재하지 않는 base ref로 실제 git diff가 실패하면 fail-closed(exit 1)여야
    한다 -- main()의 except subprocess.CalledProcessError 분기를 실제 git으로 실행."""
    _init_repo_with_generated_file(tmp_path)

    exit_code = check_generated_dir_integrity.main(
        ["--base", "does-not-exist-ref", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    assert exit_code == 1


def test_git_binary_missing_fails_closed_instead_of_silently_passing(tmp_path: Path) -> None:
    """실패주입: git 실행 파일이 없어 subprocess.run이 OSError를 내면 main()이 조용히
    0을 반환(거짓 OK)하지 않아야 한다."""
    _init_repo_with_generated_file(tmp_path)

    def _raise_missing_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git executable not found")

    original_run = check_generated_dir_integrity.subprocess.run
    check_generated_dir_integrity.subprocess.run = _raise_missing_git
    try:
        raised = False
        try:
            check_generated_dir_integrity.main(
                ["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)]
            )
        except FileNotFoundError:
            raised = True
        assert raised, "git 바이너리 부재가 거짓 OK(exit 0)로 위장됐다"
    finally:
        check_generated_dir_integrity.subprocess.run = original_run


def test_lookalike_directory_named_generated_something_is_not_falsely_flagged(
    tmp_path: Path,
) -> None:
    """negative(매칭 경계): 디렉터리 이름 자체가 `generated`인 경로만 잡아야 한다 --
    `generated_reports/` 같은 형제 디렉터리를 접두어 문자열 비교로 오탐하면 안 된다."""
    _init_repo_with_generated_file(tmp_path)

    lookalike_dir = tmp_path / "src" / "exchanges" / "kis" / "generated_reports"
    lookalike_dir.mkdir(parents=True)
    (lookalike_dir / "summary.py").write_text("# not a generated/ artifact\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "add lookalike dir")
    _run_git(tmp_path, "branch", "-f", "base", "HEAD")

    (lookalike_dir / "summary.py").unlink()
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "delete lookalike file, not generated/")

    exit_code = check_generated_dir_integrity.main(
        ["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    assert exit_code == 0  # generated_reports/ 는 generated/ 가 아니므로 오탐하면 안 된다
    assert not check_generated_dir_integrity.is_generated_path(
        "src/exchanges/kis/generated_reports/summary.py"
    )
    assert check_generated_dir_integrity.is_generated_path(
        "src/exchanges/kis/generated/account_tr_labels.py"
    )


# ---------------------------------------------------------------------------
# 성능단언
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_find_deleted_generated_files_completes_within_time_budget(perf_budget) -> None:
    """성능단언: 대규모 PR(수천 개 삭제 파일)에서도 generated/ 스캔이 예산 내에 끝나는지."""
    deleted_files = [f"docs/generated/report_{i}.py" for i in range(4000)]
    deleted_files += [f"tests/unit/fixtures/case_{i}.py" for i in range(4000)]
    deleted_files.append("src/exchanges/kis/generated/account_tr_labels.py")
    assert len(deleted_files) > 1000

    violations = None

    def _run() -> None:
        nonlocal violations
        violations = check_generated_dir_integrity.find_deleted_generated_files(deleted_files)

    sample = perf_budget.assert_within(_run, budget_ms=1000.0, label="generated/ 삭제 스캔")
    print(f"[generated_dir_integrity scan] {perf_budget.describe(sample, budget_ms=1000.0)}")

    assert violations == ["docs/generated/report_" + str(i) + ".py" for i in range(4000)] + [
        "src/exchanges/kis/generated/account_tr_labels.py"
    ]
