"""scripts/check_migration_chain.py 단위 테스트 — task-1988 병합 회귀.

task-10864: 680줄 → 3파일 분할(CLAUDE.md ADR-2026-09-10-C LOC 규율) 중 세 번째
파일. 기본 체인 로직/범용 병합 패턴은 test_check_migration_chain.py, task-1814/
task-1987 회귀는 test_check_migration_chain_merge_1814_1987.py에 있다.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
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


def _shadow_real_versions_dir(tmp_path: Path, *, exclude: str | None = None) -> Path:
    real_versions_dir = ROOT / "src" / "db" / "migrations" / "versions"
    shadow = tmp_path / "versions"
    shadow.mkdir()
    for path in real_versions_dir.glob("*.py"):
        if path.name == "__init__.py" or path.name == exclude:
            continue
        (shadow / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return shadow


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
# 했다. 이 병합 리비전 자체는 test_check_migration_chain.py의
# "task-2099/a3096b99" 절에서 이미 범용 픽스처(a/b/c/d)로 dual-head 해소·
# 오탈자 실패·게이트재현을 검증하고 있으나, task-1814/1987과 동일하게
# "실제 파일"의 두 부모가 정확히 무엇인지, no-op 본문이 이후 편집으로 깨지지
# 않는지, 부모 하나가 통째로 누락되면(오탈자가 아니라 완전 누락) 다시
# 적색이 되는지, 병합이 있어도 그 위 새 미병합 브랜치는 여전히 잡히는지
# (D3 적대적), 동시/반복 실행에도 결과가 안정적인지(D3 리플레이)는 이 leaf
# 전까지 증명되지 않았다. 아래 8개로 그 공백을 메운다. (94da854f522f,
# a2c4f9e1b3d5 모두 저장소 내 이 병합 외에 다른 자식이 없으므로 어느 쪽을
# 누락시켜도 동일하게 새 head가 드러난다.)
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
