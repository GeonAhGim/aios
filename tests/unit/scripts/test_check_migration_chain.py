"""scripts/check_migration_chain.py 단위 테스트 — PLT-38.

DoD: 두 head fixture가 FAIL하는 것을 직접 단언한다.
DB·네트워크 접근 없음 — 임시 디렉터리만 쓴다.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
# check_migration_chain — merge revision (task-1814/f03d9ff FA-0a batch A ×
# DC-27 dual-head 회귀 방지, DEPTH_FA.md D0 스텁 판정 보강 — task-3015)
#
# task-1814가 고친 실제 장애: rebase 도중 origin/main에 DC-27(ff56c0e3e1ea)이
# 먼저 병합되어 FA-0a batch A(ccfb229d760d)와 head가 갈라졌다 — 표준 alembic
# merge(24af9c37d76f, upgrade/downgrade 모두 pass인 no-op)로 합쳤다.
# DEPTH_FA.md 감사(2026-09-10)는 이 커밋이 테스트 파일 0개인 완전 스텁(D0)
# 이라고 판정했다. 아래는 이 병합이 실제로 두 head를 정확히 해소하는지
# (contract), 부모 하나가 누락되거나 오탈자·문법 오류로 깨지면 다시
# 게이트가 적색이 되는지(실패주입·negative), 병합 리비전 자체를 제거하면
# 원래 장애가 재현되는지(게이트 적색 재현), 실제 저장소 규모(116개 리비전)
# 에서도 검사가 예산 내에 끝나는지(수치 성능 단언), 동시/반복 실행에도
# 결과가 안정적인지(D3 다중 인스턴스·리플레이), 그리고 이 병합이 존재해도
# 그 위에 새로 생긴 미병합 브랜치는 여전히 잡히는지(D3 적대적)를 검증한다.
# ---------------------------------------------------------------------------

_TASK_1814_MERGE_FILE = "24af9c37d76f_merge_heads_fa0a_batch_a_and_dc27.py"
_TASK_1814_PARENTS = ("ccfb229d760d", "ff56c0e3e1ea")


def _shadow_real_versions_dir(tmp_path: Path, *, exclude: str | None = None) -> Path:
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    shadow = tmp_path / "versions"
    shadow.mkdir()
    for path in real_versions_dir.glob("*.py"):
        if path.name == "__init__.py" or path.name == exclude:
            continue
        (shadow / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return shadow


def test_task_1814_merge_revision_declares_exactly_batch_a_and_dc27_parents() -> None:
    """병합 리비전의 down_revision이 정확히 두 부모(FA-0a batch A, DC-27)인지 정적 확인."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    record = check_migration_chain.parse_revision_file(real_versions_dir / _TASK_1814_MERGE_FILE)

    assert record is not None
    assert record.revision == "24af9c37d76f"
    assert set(record.down_revisions) == set(_TASK_1814_PARENTS)


def test_task_1814_merge_revision_upgrade_downgrade_bodies_are_true_noops() -> None:
    """ "no-op 병합"이라는 DEPTH_FA.md 판정이 이후 편집으로 깨지지 않는지 AST로 고정한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    source = (real_versions_dir / _TASK_1814_MERGE_FILE).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = {
        node.name: node
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"upgrade", "downgrade"}
    }

    assert set(functions) == {"upgrade", "downgrade"}
    for name, func in functions.items():
        assert len(func.body) == 1, f"{name}()가 pass 하나가 아니라 실질 연산을 담고 있다"
        assert isinstance(func.body[0], ast.Pass), f"{name}()가 더 이상 no-op이 아니다"


def test_task_1814_merge_missing_one_declared_parent_reintroduces_dual_head(
    tmp_path: Path,
) -> None:
    """실패주입: 부모 하나가 누락되면(불완전 리베이스) 다시 다중 head로 적색이 되어야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1814_MERGE_FILE).write_text(
        MERGE_REVISION_TEMPLATE.format(
            revision="24af9c37d76f", down_revisions=(_TASK_1814_PARENTS[0],)
        ),
        encoding="utf-8",
    )

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1814_merge_parent_typo_breaks_chain_and_reflags_dual_head(
    tmp_path: Path,
) -> None:
    """negative: 부모 id에 오탈자가 생기면 체인 끊김과 다중 head가 동시에 잡혀야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1814_MERGE_FILE).write_text(
        MERGE_REVISION_TEMPLATE.format(
            revision="24af9c37d76f",
            down_revisions=(_TASK_1814_PARENTS[0], _TASK_1814_PARENTS[1] + "-typo"),
        ),
        encoding="utf-8",
    )

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("끊김" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1814_merge_revision_corrupted_syntax_fails_closed(tmp_path: Path) -> None:
    """실패주입: 병합 리비전 파일이 문법 오류로 깨지면 조용히 통과하지 않고 적색이어야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1814_MERGE_FILE).write_text("def upgrade(:\n    pass\n", encoding="utf-8")

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("정적 파싱 실패" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)  # 두 브랜치가 다시 미병합 상태
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1814_merge_still_flags_new_unmerged_sibling_branch_as_head(
    tmp_path: Path,
) -> None:
    """D3 적대적: 1814 병합이 있어도 그 위에 새 미병합 브랜치가 생기면 여전히 다중 head로 잡힌다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    _write_revision(shadow, "adversarial-branch", _TASK_1814_PARENTS[0])

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_check_migration_chain_real_versions_dir_completes_within_time_budget() -> None:
    """성능단언: 실제 저장소(116개 리비전) 전수 체인 검사가 예산 내에 끝나는지 수치로 확인한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    revision_count = len([p for p in real_versions_dir.glob("*.py") if p.name != "__init__.py"])
    assert revision_count > 100  # 이 벤치마크가 무의미해지지 않도록 규모를 보장

    start = time.perf_counter()
    issues = check_migration_chain.find_chain_issues(real_versions_dir)
    elapsed = time.perf_counter() - start

    assert issues == []
    assert elapsed < 2.0, f"마이그레이션 체인 검사가 {elapsed:.3f}s — 예산(2.0s) 초과"


def test_task_1814_merge_check_is_deterministic_under_concurrent_replay() -> None:
    """D3: 여러 워커가 동시에 반복 실행해도(리플레이·다중 인스턴스) 결과가 항상 동일해야 한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _: check_migration_chain.find_chain_issues(real_versions_dir), range(16)
            )
        )

    assert all(result == [] for result in results)
    assert len({tuple(result) for result in results}) == 1


def test_removing_task_1814_merge_revision_reproduces_original_dual_head_failure(
    tmp_path: Path,
) -> None:
    """게이트재현: task-1814(f03d9ff) 병합 리비전을 걷어내면 원래 dual-head 장애가 재현된다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    assert (real_versions_dir / _TASK_1814_MERGE_FILE).is_file()

    shadow = _shadow_real_versions_dir(tmp_path, exclude=_TASK_1814_MERGE_FILE)

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


# ---------------------------------------------------------------------------
# check_migration_chain — merge revision (task-1987/117b40d5 FA-0a batch B ×
# calendar source widen dual-head 회귀 방지, DEPTH_FA.md D0 스텁 판정 보강 —
# task-3017)
#
# task-1987이 고친 실제 장애: rebase 도중 origin/main에 캘린더 source 컬럼 폭
# 확장(6325757fd371)이 먼저 병합되어 FA-0a batch B(f6b25409405e, ledger·
# positions·market-data tenant_id FK 정정)와 head가 갈라졌다 — 표준 alembic
# merge(2e35eea547f2, upgrade/downgrade 모두 pass인 no-op)로 합쳤다.
# DEPTH_FA.md 감사는 이 커밋이 테스트 파일 0개인 완전 스텁(D0)이라고 판정
# 했다. 아래는 task-1814/24af9c37d76f(task-3015)와 같은 구조로: 병합이 실제로
# 두 head를 정확히 해소하는지(contract), 부모 하나가 누락되거나 오탈자·
# 문법 오류로 깨지면 다시 게이트가 적색이 되는지(실패주입·negative), 병합
# 리비전 자체를 제거하면 원래 장애가 재현되는지(게이트 적색 재현), 실제
# 저장소 규모에서도 검사가 예산 내에 끝나는지(수치 성능 단언), 동시/반복
# 실행에도 결과가 안정적인지(D3 다중 인스턴스·리플레이), 그리고 이 병합이
# 존재해도 그 위에 새로 생긴 미병합 브랜치는 여전히 잡히는지(D3 적대적)를
# 검증한다.
# ---------------------------------------------------------------------------

_TASK_1987_MERGE_FILE = "2e35eea547f2_merge_heads_fa0a_batch_b_and_calendar_source.py"
_TASK_1987_PARENTS = ("f6b25409405e", "6325757fd371")


def test_task_1987_merge_revision_declares_exactly_batch_b_and_calendar_parents() -> None:
    """병합 리비전의 down_revision이 정확히 두 부모(FA-0a batch B, calendar source)인지 확인."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    record = check_migration_chain.parse_revision_file(real_versions_dir / _TASK_1987_MERGE_FILE)

    assert record is not None
    assert record.revision == "2e35eea547f2"
    assert set(record.down_revisions) == set(_TASK_1987_PARENTS)


def test_task_1987_merge_revision_upgrade_downgrade_bodies_are_true_noops() -> None:
    """ "no-op 병합"이라는 DEPTH_FA.md 판정이 이후 편집으로 깨지지 않는지 AST로 고정한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    source = (real_versions_dir / _TASK_1987_MERGE_FILE).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = {
        node.name: node
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"upgrade", "downgrade"}
    }

    assert set(functions) == {"upgrade", "downgrade"}
    for name, func in functions.items():
        assert len(func.body) == 1, f"{name}()가 pass 하나가 아니라 실질 연산을 담고 있다"
        assert isinstance(func.body[0], ast.Pass), f"{name}()가 더 이상 no-op이 아니다"


def test_task_1987_merge_missing_one_declared_parent_reintroduces_dual_head(
    tmp_path: Path,
) -> None:
    """실패주입: 부모 하나가 누락되면(불완전 리베이스) 다시 다중 head로 적색이 되어야 한다.

    6325757fd371(calendar source)은 저장소에서 이미 별도 병합(7020473c9e74)이
    참조하는 갈래라 그것만 빠지면 head가 되지 않는다 — 이 병합만 참조하는
    f6b25409405e(batch B)를 빼야 실제로 새 head가 드러난다.
    """
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1987_MERGE_FILE).write_text(
        MERGE_REVISION_TEMPLATE.format(
            revision="2e35eea547f2", down_revisions=(_TASK_1987_PARENTS[1],)
        ),
        encoding="utf-8",
    )

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1987_merge_parent_typo_breaks_chain_and_reflags_dual_head(
    tmp_path: Path,
) -> None:
    """negative: 부모 id에 오탈자가 생기면 체인 끊김과 다중 head가 동시에 잡혀야 한다.

    오탈자는 이 병합만 참조하는 f6b25409405e(batch B) 쪽에 내야 한다 —
    6325757fd371(calendar source)에 내면 다른 병합(7020473c9e74)이 이미 그
    갈래를 참조하고 있어 다중 head로 드러나지 않는다.
    """
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1987_MERGE_FILE).write_text(
        MERGE_REVISION_TEMPLATE.format(
            revision="2e35eea547f2",
            down_revisions=(_TASK_1987_PARENTS[0] + "-typo", _TASK_1987_PARENTS[1]),
        ),
        encoding="utf-8",
    )

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("끊김" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1987_merge_revision_corrupted_syntax_fails_closed(tmp_path: Path) -> None:
    """실패주입: 병합 리비전 파일이 문법 오류로 깨지면 조용히 통과하지 않고 적색이어야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1987_MERGE_FILE).write_text("def upgrade(:\n    pass\n", encoding="utf-8")

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("정적 파싱 실패" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)  # 두 브랜치가 다시 미병합 상태
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1987_merge_still_flags_new_unmerged_sibling_branch_as_head(
    tmp_path: Path,
) -> None:
    """D3 적대적: 1987 병합이 있어도 그 위에 새 미병합 브랜치가 생기면 여전히 다중 head로 잡힌다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    _write_revision(shadow, "adversarial-branch-1987", _TASK_1987_PARENTS[0])

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1987_merge_check_is_deterministic_under_concurrent_replay() -> None:
    """D3: 여러 워커가 동시에 반복 실행해도(리플레이·다중 인스턴스) 결과가 항상 동일해야 한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _: check_migration_chain.find_chain_issues(real_versions_dir), range(16)
            )
        )

    assert all(result == [] for result in results)
    assert len({tuple(result) for result in results}) == 1


def test_removing_task_1987_merge_revision_reproduces_original_dual_head_failure(
    tmp_path: Path,
) -> None:
    """게이트재현: task-1987(117b40d5) 병합 리비전을 걷어내면 원래 dual-head 장애가 재현된다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    assert (real_versions_dir / _TASK_1987_MERGE_FILE).is_file()

    shadow = _shadow_real_versions_dir(tmp_path, exclude=_TASK_1987_MERGE_FILE)

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


# ---------------------------------------------------------------------------
# check_migration_chain — merge revision (task-1988/a3096b99 FA-0a batch C ×
# FA-10 bitemporal projections dual-head 회귀 방지, DEPTH_FA.md D0 스텁 판정
# 보강 — task-3022)
#
# task-1988이 고친 실제 장애: rebase 도중 origin/main에 FA-10 bitemporal
# projections(a2c4f9e1b3d5)가 먼저 병합되어 FA-0a batch C(94da854f522f, 리스크·
# 포트폴리오·엔티티 tenant_id FK 정정)와 head가 갈라졌다 — 표준 alembic
# merge(6877947783a6, upgrade/downgrade 모두 pass인 no-op)로 합쳤다.
# DEPTH_FA.md 감사는 이 커밋이 테스트 파일 0개인 완전 스텁(D0)이라고 판정
# 했다. 이 병합 리비전 자체는 위 "task-2099/a3096b99" 절에서 이미 범용
# 픽스처(a/b/c/d)로 dual-head 해소·오탈자 실패·게이트재현을 검증하고 있으나,
# task-1814/1987과 동일하게 "실제 파일"의 두 부모가 정확히 무엇인지, no-op
# 본문이 이후 편집으로 깨지지 않는지, 부모 하나가 통째로 누락되면(오탈자가
# 아니라 완전 누락) 다시 적색이 되는지, 병합이 있어도 그 위 새 미병합
# 브랜치는 여전히 잡히는지(D3 적대적), 동시/반복 실행에도 결과가 안정적인지
# (D3 리플레이)는 이 leaf 전까지 증명되지 않았다. 아래 8개로 그 공백을
# 메운다. (94da854f522f, a2c4f9e1b3d5 모두 저장소 내 이 병합 외에 다른
# 자식이 없으므로 어느 쪽을 누락시켜도 동일하게 새 head가 드러난다.)
# ---------------------------------------------------------------------------

_TASK_1988_MERGE_FILE = "6877947783a6_merge_heads_fa0a_batch_c_and_fa10_.py"
_TASK_1988_PARENTS = ("94da854f522f", "a2c4f9e1b3d5")


def test_task_1988_merge_revision_declares_exactly_batch_c_and_fa10_parents() -> None:
    """병합 리비전의 down_revision이 정확히 두 부모(FA-0a batch C, FA-10 bitemporal)인지 확인."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    record = check_migration_chain.parse_revision_file(real_versions_dir / _TASK_1988_MERGE_FILE)

    assert record is not None
    assert record.revision == "6877947783a6"
    assert set(record.down_revisions) == set(_TASK_1988_PARENTS)


def test_task_1988_merge_revision_upgrade_downgrade_bodies_are_true_noops() -> None:
    """ "no-op 병합"이라는 DEPTH_FA.md 판정이 이후 편집으로 깨지지 않는지 AST로 고정한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    source = (real_versions_dir / _TASK_1988_MERGE_FILE).read_text(encoding="utf-8")
    tree = ast.parse(source)

    functions = {
        node.name: node
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"upgrade", "downgrade"}
    }

    assert set(functions) == {"upgrade", "downgrade"}
    for name, func in functions.items():
        assert len(func.body) == 1, f"{name}()가 pass 하나가 아니라 실질 연산을 담고 있다"
        assert isinstance(func.body[0], ast.Pass), f"{name}()가 더 이상 no-op이 아니다"


def test_task_1988_merge_missing_one_declared_parent_reintroduces_dual_head(
    tmp_path: Path,
) -> None:
    """실패주입: 부모 하나가 누락되면(불완전 리베이스) 다중 head로 다시 적색이 되어야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1988_MERGE_FILE).write_text(
        MERGE_REVISION_TEMPLATE.format(
            revision="6877947783a6", down_revisions=(_TASK_1988_PARENTS[0],)
        ),
        encoding="utf-8",
    )

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1988_merge_parent_typo_breaks_chain_and_reflags_dual_head(
    tmp_path: Path,
) -> None:
    """negative: 부모 id에 오탈자가 생기면 체인 끊김과 다중 head가 동시에 잡혀야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1988_MERGE_FILE).write_text(
        MERGE_REVISION_TEMPLATE.format(
            revision="6877947783a6",
            down_revisions=(_TASK_1988_PARENTS[0], _TASK_1988_PARENTS[1] + "-typo"),
        ),
        encoding="utf-8",
    )

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("끊김" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1988_merge_revision_corrupted_syntax_fails_closed(tmp_path: Path) -> None:
    """실패주입: 병합 리비전 파일이 문법 오류로 깨지면 조용히 통과하지 않고 적색이어야 한다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    (shadow / _TASK_1988_MERGE_FILE).write_text("def upgrade(:\n    pass\n", encoding="utf-8")

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("정적 파싱 실패" in issue for issue in issues)
    assert any("다중 head" in issue for issue in issues)  # 두 브랜치가 다시 미병합 상태
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1988_merge_still_flags_new_unmerged_sibling_branch_as_head(
    tmp_path: Path,
) -> None:
    """D3 적대적: 1988 병합이 있어도 그 위에 새 미병합 브랜치가 생기면 여전히 다중 head로 잡힌다."""
    shadow = _shadow_real_versions_dir(tmp_path)
    _write_revision(shadow, "adversarial-branch-1988", _TASK_1988_PARENTS[0])

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1


def test_task_1988_merge_check_is_deterministic_under_concurrent_replay() -> None:
    """D3: 여러 워커가 동시에 반복 실행해도(리플레이·다중 인스턴스) 결과가 항상 동일해야 한다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _: check_migration_chain.find_chain_issues(real_versions_dir), range(16)
            )
        )

    assert all(result == [] for result in results)
    assert len({tuple(result) for result in results}) == 1


def test_removing_task_1988_merge_revision_reproduces_original_dual_head_failure(
    tmp_path: Path,
) -> None:
    """게이트재현: task-1988(a3096b99) 병합 리비전을 걷어내면 원래 dual-head 장애가 재현된다."""
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    assert (real_versions_dir / _TASK_1988_MERGE_FILE).is_file()

    shadow = _shadow_real_versions_dir(tmp_path, exclude=_TASK_1988_MERGE_FILE)

    issues = check_migration_chain.find_chain_issues(shadow)

    assert any("다중 head" in issue for issue in issues)
    assert check_migration_chain.main(["--versions-dir", str(shadow)]) == 1
