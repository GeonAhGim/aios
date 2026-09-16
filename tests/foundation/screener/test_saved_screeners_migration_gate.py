"""U-1a `saved_screeners` 마이그레이션(052b26dfb97b) 게이트 적색 재현.

`scripts/check_migration_chain.py::find_chain_issues`가 실제로 다중 head를
잡아내는지, 임시 디렉터리에 이 리프의 리비전과 같은 모양(같은 down_revision)의
두 파일을 만들어 재현한다 — 이 리프가 브랜치 규율을 어기면(§ CLAUDE.md
Frequent mistakes #9) 이 게이트가 적색으로 막는다는 것을 증명한다.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_migration_chain import find_chain_issues

_REAL_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src" / "db" / "migrations" / "versions"


def _write_revision(directory: Path, *, filename: str, revision: str, down_revision: str) -> None:
    (directory / filename).write_text(
        f'revision: str = "{revision}"\ndown_revision: str | None = "{down_revision}"\n',
        encoding="utf-8",
    )


def test_real_versions_dir_has_no_chain_issues() -> None:
    """녹색 기준선: 052b26dfb97b가 실제로 단일 head 체인에 올바르게 붙어 있다."""
    assert find_chain_issues(_REAL_VERSIONS_DIR) == []


def test_gate_catches_duplicate_head_at_saved_screeners_parent(tmp_path: Path) -> None:
    """적색 재현: 052b26dfb97b와 같은 부모(b4bb1b750621)를 가리키는 두 번째
    리비전이 동시에 존재하면(브랜치 실수) 게이트가 다중 head로 거부한다."""
    _write_revision(
        tmp_path,
        filename="052b26dfb97b_saved_screeners.py",
        revision="052b26dfb97b",
        down_revision="b4bb1b750621",
    )
    _write_revision(
        tmp_path,
        filename="aaaaaaaaaaaa_conflicting_branch.py",
        revision="aaaaaaaaaaaa",
        down_revision="b4bb1b750621",
    )
    # 체인의 나머지(부모 b4bb1b750621까지)를 알 필요는 없다 — 이 검사는
    # 알려진 리비전 집합 안에서만 head를 판정하므로, 두 리비전이 서로 다른
    # head로 남는다는 것만으로 다중 head가 재현된다.

    issues = find_chain_issues(tmp_path)

    assert any("다중 head" in issue for issue in issues)
