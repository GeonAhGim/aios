"""U-1a `saved_screeners` 마이그레이션(052b26dfb97b) 게이트 적색 재현.

`scripts/check_migration_chain.py::find_chain_issues`가 실제로 다중 head를
잡아내는지, 임시 디렉터리에 이 리프의 리비전과 같은 모양(같은 down_revision)의
두 파일을 만들어 재현한다 — 이 리프가 브랜치 규율을 어기면(§ CLAUDE.md
Frequent mistakes #9) 이 게이트가 적색으로 막는다는 것을 증명한다.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_migration_chain import find_chain_issues, parse_revision_file

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


def test_gate_catches_broken_down_revision_chain(tmp_path: Path) -> None:
    """부정 테스트: 052b26dfb97b가 존재하지 않는 부모를 가리키면(오타 등)
    체인 끊김으로 거부한다 — alembic이 upgrade 시 그 부모를 찾지 못해 실패하는
    상황을 정적 검사 단계에서 미리 잡아낸다."""
    _write_revision(
        tmp_path,
        filename="052b26dfb97b_saved_screeners.py",
        revision="052b26dfb97b",
        down_revision="ffffffffffff",
    )

    issues = find_chain_issues(tmp_path)

    assert any("down_revision 끊김" in issue for issue in issues)


def test_gate_catches_cycle_at_saved_screeners_revision(tmp_path: Path) -> None:
    """부정 테스트: 052b26dfb97b와 그 부모가 서로를 가리키는 순환이 생기면
    거부한다 — 순환 체인은 alembic이 어떤 순서로도 적용할 수 없다."""
    _write_revision(
        tmp_path,
        filename="052b26dfb97b_saved_screeners.py",
        revision="052b26dfb97b",
        down_revision="b4bb1b750621",
    )
    _write_revision(
        tmp_path,
        filename="b4bb1b750621_parent.py",
        revision="b4bb1b750621",
        down_revision="052b26dfb97b",
    )

    issues = find_chain_issues(tmp_path)

    assert any("순환 참조" in issue for issue in issues)


def test_gate_catches_duplicate_revision_id(tmp_path: Path) -> None:
    """부정 테스트: 같은 revision id(052b26dfb97b)를 가진 두 파일이 동시에
    존재하면(머지 충돌 잔재 등) 중복으로 거부한다."""
    _write_revision(
        tmp_path,
        filename="052b26dfb97b_saved_screeners.py",
        revision="052b26dfb97b",
        down_revision="b4bb1b750621",
    )
    _write_revision(
        tmp_path,
        filename="052b26dfb97b_duplicate_copy.py",
        revision="052b26dfb97b",
        down_revision="b4bb1b750621",
    )

    issues = find_chain_issues(tmp_path)

    assert any("중복 revision id" in issue for issue in issues)


def test_parse_revision_file_rejects_unparseable_syntax(tmp_path: Path) -> None:
    """실패주입: 052b26dfb97b 리비전 파일이 문법 오류로 깨져 있으면(예: 저장 중
    잘림) `parse_revision_file`이 예외로 죽지 않고 `None`을 반환해 fail-closed로
    처리하며, `find_chain_issues`는 이를 '식별 불가'로 보고한다."""
    broken = tmp_path / "052b26dfb97b_saved_screeners.py"
    broken.write_text("revision: str = 'unterminated\n", encoding="utf-8")

    assert parse_revision_file(broken) is None

    issues = find_chain_issues(tmp_path)

    assert any("식별 불가" in issue for issue in issues)
