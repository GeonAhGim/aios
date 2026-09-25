"""scripts/check_zone_diff.py 단위 테스트 — PLT-38.

DoD: FROZEN diff fixture가 FAIL하는 것을 직접 단언한다.
DB·네트워크 접근 없음 — 임시 디렉터리와 로컬 git 명령(네트워크 없는 `git init`류)만 쓴다.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses는 cls.__module__을 sys.modules에서 찾는다
    spec.loader.exec_module(module)
    return module


check_zone_diff = _load_module("check_zone_diff", SCRIPTS_DIR / "check_zone_diff.py")


# ---------------------------------------------------------------------------
# check_zone_diff
# ---------------------------------------------------------------------------


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo_with_manifest(repo: Path) -> None:
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "test")
    (repo / ".aios-zone").write_text(
        'zones:\n  FROZEN:\n    - "aios/kernel/**"\n  OPEN:\n    - "docs/**"\n',
        encoding="utf-8",
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("base\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "base")
    _run_git(repo, "branch", "base")


def test_frozen_zone_diff_fails(tmp_path: Path) -> None:
    _init_repo_with_manifest(tmp_path)

    kernel_dir = tmp_path / "aios" / "kernel" / "policy"
    kernel_dir.mkdir(parents=True)
    (kernel_dir / "rule.py").write_text("# frozen change\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "touch frozen zone")

    exit_code = check_zone_diff.main(["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)])

    assert exit_code == 1


def test_non_frozen_diff_passes(tmp_path: Path) -> None:
    _init_repo_with_manifest(tmp_path)

    (tmp_path / "docs" / "other.md").write_text("more docs\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "touch open zone")

    exit_code = check_zone_diff.main(["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)])

    assert exit_code == 0


def test_missing_manifest_fails(tmp_path: Path) -> None:
    _run_git(tmp_path, "init", "-q")

    exit_code = check_zone_diff.main(["--base", "HEAD", "--head", "HEAD", "--repo", str(tmp_path)])

    assert exit_code == 1


@pytest.mark.perf
def test_find_frozen_violations_completes_within_time_budget() -> None:
    """성능단언: 대규모 PR(수천 개 변경 파일)에서도 FROZEN 존 스캔이 예산 내에 끝나는지 확인."""
    frozen_patterns = ["aios/kernel/**", "src/core/strategy/**"]
    changed_files = [f"docs/generated/report_{i}.md" for i in range(4000)]
    changed_files += [f"tests/unit/generated/test_{i}.py" for i in range(4000)]
    changed_files.append("aios/kernel/policy/rule.py")  # 위반 1건을 대량 diff 속에 섞는다
    assert len(changed_files) > 1000  # 이 벤치마크가 무의미해지지 않도록 규모를 보장

    start = time.perf_counter()
    violations = check_zone_diff.find_frozen_violations(changed_files, frozen_patterns)
    elapsed = time.perf_counter() - start

    assert violations == ["aios/kernel/policy/rule.py"]
    assert elapsed < 1.0, f"FROZEN 존 위반 스캔이 {elapsed:.3f}s — 예산(1.0s) 초과"


# ---------------------------------------------------------------------------
# check_zone_diff — DEEPEN(task-4119): negative + 실패주입 보강.
#
# task-4084 DEEPEN 기준으로 negative test <3, 실패주입 마커 없음이라고
# 지적됐다. 위 4개 테스트는 양성 1개(non_frozen)·형식적 negative 2개
# (frozen_zone_diff/missing_manifest)·성능단언 1개뿐이었고, main()의
# `except subprocess.CalledProcessError`(git diff 실패 시 fail-closed 경로)는
# 어떤 테스트도 실행한 적이 없었다. 아래 4개로: (1) git 자체가 없는
# 환경(OSError)에 주입해도 조용히 통과하지 않는지(실패주입), (2) 존재하지
# 않는 base ref로 실제 git diff가 실패했을 때 fail-closed(exit 1)인지
# (negative, except 분기 최초 커버), (3) 깨진 YAML 매니페스트가 조용히
# 통과로 위장하지 않는지(negative, 프로세스 수준), (4) FROZEN 패턴과
# 이름이 비슷한 형제 디렉터리를 오탐(false positive)하지 않는지
# (negative, 매칭 경계)를 검증한다.
# ---------------------------------------------------------------------------


def test_git_binary_missing_fails_closed_instead_of_silently_passing(tmp_path: Path) -> None:
    """실패주입: git 실행 파일이 없어 subprocess.run이 OSError를 내면 main()이 조용히 0을
    반환(거짓 OK)하지 않아야 한다 — CalledProcessError만 잡는 except 분기 밖의 의존성 실패다."""
    _init_repo_with_manifest(tmp_path)

    def _raise_missing_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git executable not found")

    original_run = check_zone_diff.subprocess.run
    check_zone_diff.subprocess.run = _raise_missing_git
    try:
        raised = False
        try:
            check_zone_diff.main(["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)])
        except FileNotFoundError:
            raised = True
        assert raised, "git 바이너리 부재가 거짓 OK(exit 0)로 위장됐다"
    finally:
        check_zone_diff.subprocess.run = original_run


def test_nonexistent_base_ref_fails_closed_via_real_git_diff_error(tmp_path: Path) -> None:
    """negative: 존재하지 않는 base ref로 실제 `git diff`가 실패하면 fail-closed(exit 1)여야
    한다 — main()의 `except subprocess.CalledProcessError` 분기를 실제 git으로 최초 실행."""
    _init_repo_with_manifest(tmp_path)

    exit_code = check_zone_diff.main(
        ["--base", "does-not-exist-ref", "--head", "HEAD", "--repo", str(tmp_path)]
    )

    assert exit_code == 1


def test_corrupt_yaml_manifest_fails_closed_at_process_level(tmp_path: Path) -> None:
    """negative: `.aios-zone`이 파싱 불가한 YAML이면 조용히 통과(exit 0)로 위장하지 않고
    프로세스가 0이 아닌 종료코드로 fail-closed 되어야 한다(실제 `python scripts/check_zone_diff.py`
    호출 — main()이 raise한 YAMLError도 인터프리터가 nonzero exit으로 승격시키는지 확인)."""
    _init_repo_with_manifest(tmp_path)
    (tmp_path / ".aios-zone").write_text("zones:\n  FROZEN: [invalid: [\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "corrupt manifest")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "check_zone_diff.py"),
            "--base",
            "base",
            "--head",
            "HEAD",
            "--repo",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "깨진 YAML 매니페스트가 exit 0(거짓 통과)으로 위장됐다"


def test_lookalike_sibling_directory_is_not_falsely_flagged_frozen(tmp_path: Path) -> None:
    """negative(매칭 경계): `aios/kernel/**`는 이름이 비슷한 형제 디렉터리
    `aios/kernel_extra/`까지 오탐(false positive)으로 차단하면 안 된다 — prefix 문자열
    비교가 아니라 `prefix + "/"` 경계로 디렉터리 구분을 강제하는지 확인한다."""
    _init_repo_with_manifest(tmp_path)

    lookalike_dir = tmp_path / "aios" / "kernel_extra"
    lookalike_dir.mkdir(parents=True)
    (lookalike_dir / "file.py").write_text("# not actually frozen\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-q", "-m", "touch lookalike sibling dir")

    exit_code = check_zone_diff.main(["--base", "base", "--head", "HEAD", "--repo", str(tmp_path)])

    assert exit_code == 0
