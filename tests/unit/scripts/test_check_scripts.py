"""scripts/check_migration_chain.py, scripts/check_zone_diff.py 단위 테스트 — PLT-38.

DoD: 두 head fixture와 FROZEN diff fixture가 각각 FAIL하는 것을 직접 단언한다.
DB·네트워크 접근 없음 — 임시 디렉터리와 로컬 git 명령(네트워크 없는 `git init`류)만 쓴다.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses는 cls.__module__을 sys.modules에서 찾는다
    spec.loader.exec_module(module)
    return module


check_migration_chain = _load_module(
    "check_migration_chain", SCRIPTS_DIR / "check_migration_chain.py"
)
check_zone_diff = _load_module("check_zone_diff", SCRIPTS_DIR / "check_zone_diff.py")


REVISION_TEMPLATE = '''"""{revision} test fixture"""
from __future__ import annotations

revision: str = "{revision}"
down_revision: str | None = {down_revision!r}
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def _write_revision(versions_dir: Path, revision: str, down_revision: str | None) -> None:
    path = versions_dir / f"{revision}_fixture.py"
    path.write_text(
        REVISION_TEMPLATE.format(revision=revision, down_revision=down_revision),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# check_migration_chain
# ---------------------------------------------------------------------------


def test_linear_chain_passes(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    _write_revision(tmp_path, "c", "b")

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0


def test_dual_head_fails(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")  # head 1: b
    _write_revision(tmp_path, "c", "a")  # head 2: c (a에서 갈라진 두 번째 head)

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


def test_broken_down_revision_fails(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "missing-revision")

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert any("끊김" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


def test_cycle_fails(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a", "b")
    _write_revision(tmp_path, "b", "a")

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert any("순환" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


def test_missing_versions_dir_fails(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert check_migration_chain.main(["--versions-dir", str(missing)]) == 1


# ---------------------------------------------------------------------------
# check_migration_chain — merge revision (task-2099/a3096b99 dual-head 회귀 방지)
#
# task-2099가 고친 실제 장애: alembic head 2개가 rebase로 발생 → 표준
# `alembic merge`(down_revision이 튜플인 no-op 리비전)로 병합. 아래는 그
# 병합 패턴 자체를 정적 검사기가 올바르게 처리하는지(양성), 병합이 깨지면
# 다시 게이트가 적색이 되는지(음성/실패 주입), 그리고 저장소의 실제 병합
# 리비전을 제거하면 원래 장애가 재현되는지(게이트 적색 재현)를 검증한다.
# ---------------------------------------------------------------------------

MERGE_REVISION_TEMPLATE = '''"""{revision} merge fixture"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "{revision}"
down_revision: str | Sequence[str] | None = {down_revisions!r}
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def _write_merge_revision(
    versions_dir: Path, revision: str, down_revisions: tuple[str, ...]
) -> None:
    path = versions_dir / f"{revision}_fixture.py"
    path.write_text(
        MERGE_REVISION_TEMPLATE.format(revision=revision, down_revisions=down_revisions),
        encoding="utf-8",
    )


def test_merge_revision_resolves_dual_head(tmp_path: Path) -> None:
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")  # branch 1 head
    _write_revision(tmp_path, "c", "a")  # branch 2 head — 병합 전에는 다중 head
    assert any(
        "다중 head" in issue for issue in check_migration_chain.find_chain_issues(tmp_path)
    )

    _write_merge_revision(tmp_path, "d", ("b", "c"))  # task-2099 방식 no-op merge

    issues = check_migration_chain.find_chain_issues(tmp_path)
    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0


def test_merge_revision_with_typo_parent_still_fails_closed(tmp_path: Path) -> None:
    """병합 리비전이 오타로 한쪽 부모를 놓치면 여전히 적색이어야 한다(fail-closed)."""
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    _write_revision(tmp_path, "c", "a")
    _write_merge_revision(tmp_path, "d", ("b", "c-typo"))

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert any("끊김" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)  # c는 여전히 미병합 head
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


def test_real_migrations_directory_has_single_head() -> None:
    """저장소의 실제 마이그레이션 체인이 현재 head 1개로 유지되는지 확인한다."""
    versions_dir = ROOT / "src" / "db" / "migrations" / "versions"

    issues = check_migration_chain.find_chain_issues(versions_dir)

    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(versions_dir)]) == 0


def test_removing_task_2099_merge_revision_reproduces_dual_head_gate_failure(
    tmp_path: Path,
) -> None:
    """task-2099(a3096b99) 병합 리비전을 걷어내면 원래 장애(다중 head)가 재현되는지 확인한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    merge_file_name = "6877947783a6_merge_heads_fa0a_batch_c_and_fa10_.py"
    assert (real_versions_dir / merge_file_name).is_file()

    shadow = tmp_path / "versions"
    shadow.mkdir()
    for path in real_versions_dir.glob("*.py"):
        if path.name in {"__init__.py", merge_file_name}:
            continue
        (shadow / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


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
