"""FA-2a — legal_entity.tenant_id의 users FK 재도입을 정적으로 차단.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2a
task-1747 DoD. ADR-2026-09-06-E가 FA-0a를 신설한 결함(테넌트 FK가
`users`를 가리키는 패턴)이 `e6b1d94a7c3f`에서 재생산됐다 — 그 결함이
알려진 유일한 과거 인스턴스임을 명시적으로 허용목록에 두고, 그 외
어떤 마이그레이션도 `legal_entity.tenant_id`를 `users`로 FK하지
못하게 소스를 검사한다(런타임이 아니라 리뷰 시점에 잡히도록)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VERSIONS_DIR = Path(__file__).resolve().parents[4] / "src" / "db" / "migrations" / "versions"
# a0e7e1454b60가 이 파일을 교정한다 — 교정 대상 자체를 예외로 남겨 둔다.
_KNOWN_HISTORICAL_OFFENDER = "e6b1d94a7c3f_fa2_entities_hierarchy.py"

_EXECUTE_BODY = re.compile(
    r'op\.execute\(\s*f?"""(?P<triple>.*?)"""|op\.execute\(\s*f?"(?P<single>[^"]*)"',
    re.DOTALL,
)
_BAD_TENANT_FK = re.compile(r"tenant_id\s+UUID[^,\n]*REFERENCES\s+users", re.IGNORECASE)


def _execute_bodies(source: str) -> list[str]:
    return [m.group("triple") or m.group("single") or "" for m in _EXECUTE_BODY.finditer(source)]


def _find_offenders(versions_dir: Path, known_offender: str) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == known_offender:
            continue
        source = path.read_text(encoding="utf-8")
        for body in _execute_bodies(source):
            if "legal_entity" not in body:
                continue
            match = _BAD_TENANT_FK.search(body)
            if match is not None:
                offenders.append((path.name, match.group(0).strip()))
    return offenders


def test_no_migration_makes_legal_entity_tenant_id_reference_users() -> None:
    offenders = _find_offenders(_VERSIONS_DIR, _KNOWN_HISTORICAL_OFFENDER)

    assert offenders == [], (
        "legal_entity.tenant_id가 users(user_id)를 FK하는 마이그레이션이 "
        f"재도입됐다(ADR-2026-09-06-E FA-0a 결함 재생산): {offenders}"
    )


def test_known_historical_offender_is_the_only_allowlisted_file() -> None:
    assert (_VERSIONS_DIR / _KNOWN_HISTORICAL_OFFENDER).exists()


def test_rejects_triple_quoted_migration_reintroducing_users_fk(tmp_path: Path) -> None:
    """불변식 위반: triple-quoted op.execute에 숨은 users FK도 잡아야 한다."""
    bad_migration = tmp_path / "z0000000_reintroduces_users_fk.py"
    bad_migration.write_text(
        'op.execute("""\n'
        "    ALTER TABLE legal_entity ADD COLUMN tenant_id UUID NOT NULL "
        "REFERENCES users(id)\n"
        '""")\n',
        encoding="utf-8",
    )

    offenders = _find_offenders(tmp_path, _KNOWN_HISTORICAL_OFFENDER)

    assert offenders == [(bad_migration.name, "tenant_id UUID NOT NULL REFERENCES users")]


def test_rejects_single_quoted_migration_reintroducing_users_fk(tmp_path: Path) -> None:
    """불변식 위반: single-quoted op.execute 문자열도 같은 취급을 받아야 한다."""
    bad_migration = tmp_path / "z0000001_reintroduces_users_fk_single.py"
    bad_migration.write_text(
        'op.execute("ALTER TABLE legal_entity ADD COLUMN tenant_id UUID REFERENCES users(id)")\n',
        encoding="utf-8",
    )

    offenders = _find_offenders(tmp_path, _KNOWN_HISTORICAL_OFFENDER)

    assert len(offenders) == 1
    assert offenders[0][0] == bad_migration.name


def test_rejects_lowercase_references_variant(tmp_path: Path) -> None:
    """불변식 위반: 대소문자를 낮춰 우회를 시도해도 잡아야 한다(re.IGNORECASE 회귀 방지)."""
    bad_migration = tmp_path / "z0000002_reintroduces_users_fk_lowercase.py"
    bad_migration.write_text(
        'op.execute("""\n'
        "    alter table legal_entity add column tenant_id uuid not null "
        "references users(id)\n"
        '""")\n',
        encoding="utf-8",
    )

    offenders = _find_offenders(tmp_path, _KNOWN_HISTORICAL_OFFENDER)

    assert len(offenders) == 1
    assert offenders[0][0] == bad_migration.name


def test_allowlisted_offender_name_is_still_skipped_even_when_reused(
    tmp_path: Path,
) -> None:
    """허용목록 파일명과 동일한 파일은 위반 내용이 있어도 스킵된다(알려진 과거 결함 전용 예외)."""
    allowlisted = tmp_path / _KNOWN_HISTORICAL_OFFENDER
    allowlisted.write_text(
        'op.execute("""\n'
        "    ALTER TABLE legal_entity ADD COLUMN tenant_id UUID REFERENCES users(id)\n"
        '""")\n',
        encoding="utf-8",
    )

    offenders = _find_offenders(tmp_path, _KNOWN_HISTORICAL_OFFENDER)

    assert offenders == []


def test_scan_fails_closed_when_a_migration_file_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입: 파일 읽기가 예외를 던지면 위반을 조용히 넘기지 말고 전파해야 한다(fail-closed)."""
    unreadable = tmp_path / "z0000003_unreadable.py"
    unreadable.write_text('op.execute("legal_entity tenant_id")\n', encoding="utf-8")

    original_read_text = Path.read_text

    def _boom(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if self.name == unreadable.name:
            raise OSError("simulated disk failure")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", _boom)

    with pytest.raises(OSError, match="simulated disk failure"):
        _find_offenders(tmp_path, _KNOWN_HISTORICAL_OFFENDER)
