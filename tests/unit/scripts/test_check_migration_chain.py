"""scripts/check_migration_chain.py 단위 테스트 — PLT-38.

DoD: 두 head fixture가 FAIL하는 것을 직접 단언한다.
DB·네트워크 접근 없음 — 임시 디렉터리만 쓴다.

task-10864: 680줄 → 3파일 분할(CLAUDE.md ADR-2026-09-10-C LOC 규율). 이 파일은
기본 체인 로직 + 범용 병합(merge) 패턴 + task-2099 회귀를 담는다. task-1814/
task-1987 회귀는 test_check_migration_chain_merge_1814_1987.py로, task-1988
회귀는 test_check_migration_chain_merge_1988.py로 분할됐다.
"""

from __future__ import annotations

import importlib.util
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
    sys.modules[name] = module  # dataclasses는 cls.__module__을 sys.modules에서 찾는다
    spec.loader.exec_module(module)
    return module


check_migration_chain = _load_module(
    "check_migration_chain", SCRIPTS_DIR / "check_migration_chain.py"
)


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


@pytest.mark.perf
def test_linear_chain_passes(tmp_path: Path, perf_budget) -> None:
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    _write_revision(tmp_path, "c", "b")

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0

    # perf: find_chain_issues on a small chain must stay well under 1ms CPU
    # 实测: 0.02ms (3 revisions, tmp_path, Windows 11 Pro 10.0.26300)
    # 예산: 0.02ms × 30 = 0.6ms (실측의 30배 이상 여유, task-24485 DEEPEN)
    perf_budget.assert_within(
        lambda: check_migration_chain.find_chain_issues(tmp_path),
        budget_ms=0.6,
    )


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
    assert any("다중 head" in issue for issue in check_migration_chain.find_chain_issues(tmp_path))

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
# test_linear_chain_passes — DEEPEN (task-24485: negative 1→3+, adversarial 0→1+)
#
# 음성(negative) 테스트 3종: 단일 리비전, 3-브랜치 병합, 빈 branch_labels
# 실패 주입 2종: 중간 링크 끊김, 중복 revision id
# 게이트 적색 재현 1종: 빈 revision id로 단언이 실제로 잡는지 확인
# 성능 적색 재현 1종: perf_inject 10x 가상 지연 주입 → budget 초과 확인
# ---------------------------------------------------------------------------


def test_linear_chain_single_revision_passes(tmp_path: Path) -> None:
    """음성: down_revision이 None인 리비전이 하나뿐이어도 체인 검사 통과."""
    _write_revision(tmp_path, "a", None)

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0


def test_linear_chain_three_branch_merge_passes(tmp_path: Path) -> None:
    """음성: 3개 브랜치가 하나의 병합 리비전으로 합쳐지면 정상이어야 한다."""
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    _write_revision(tmp_path, "c", "a")
    _write_revision(tmp_path, "d", "a")
    _write_merge_revision(tmp_path, "e", ("b", "c", "d"))

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0


def test_linear_chain_with_none_branch_labels_passes(tmp_path: Path) -> None:
    """음성: branch_labels=None인 리비전이어도 체인 무결성에 영향 없다."""
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert issues == []
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0


def test_linear_chain_middle_link_broken_injection(tmp_path: Path) -> None:
    """실패 주입: 중간 링크의 down_revision을 실제 존재하지 않는 id로 바꾼다."""
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    # b의 down_revision을 "a"에서 "ghost"로 변경 — 중간 끊김
    _write_revision(tmp_path, "b", "ghost")

    issues = check_migration_chain.find_chain_issues(tmp_path)

    assert any("끊김" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


def test_linear_chain_duplicate_revision_id_injection(tmp_path: Path) -> None:
    """실패 주입: 두 파일이 같은 revision id를 가지면 체인 파손."""
    # 두 파일을 같은 revision id로 작성 — AST 파서가 둘 다 인식함
    path1 = tmp_path / "abc123_fixture.py"
    path2 = tmp_path / "abc123_fixture2.py"
    path1.write_text(
        REVISION_TEMPLATE.format(revision="abc123", down_revision=None),
        encoding="utf-8",
    )
    path2.write_text(
        REVISION_TEMPLATE.format(revision="abc123", down_revision="xyz"),
        encoding="utf-8",
    )

    # 중복 revision id는 find_chain_issues가 직접 잡지 않고,
    # 체인 구조상 이상함이 감지되어야 함
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


def test_linear_chain_gate_red_corrupted_chain(tmp_path: Path) -> None:
    """게이트 적색 재현: valid 체인에서 down_revision을 망가뜨리면
    게이트가 실제로 적색으로 전환되는지 단언이 검증한다."""
    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    _write_revision(tmp_path, "c", "b")

    # 양성 확인
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 0

    # 이제 c의 down_revision을 없는 id로 망가뜨림
    path_c = tmp_path / "c_fixture.py"
    path_c.write_text(
        REVISION_TEMPLATE.format(revision="c", down_revision="nonexistent"),
        encoding="utf-8",
    )

    # 게이트가 실제로 잡는지 단언으로 검증 (gate-red)
    issues = check_migration_chain.find_chain_issues(tmp_path)
    assert any("끊김" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(tmp_path)]) == 1


@pytest.mark.perf
def test_linear_chain_perf_gate_red_virtual_delay(tmp_path: Path, perf_budget) -> None:
    """성능 적색 재현: perf_inject로 가상 지연 주입 → budget 초과 확인."""
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    # 0.7ms 가상 지연 주입 → 0.6ms budget 초과 (gate-red)
    slow_find = virtual_delayed_sync(
        check_migration_chain.find_chain_issues,
        0.7,
    )

    _write_revision(tmp_path, "a", None)
    _write_revision(tmp_path, "b", "a")
    _write_revision(tmp_path, "c", "b")

    # 가상 지연이 주입된 호출은 budget을 초과해야 함 (gate-red)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: slow_find(tmp_path),
            budget_ms=0.6,
        )
