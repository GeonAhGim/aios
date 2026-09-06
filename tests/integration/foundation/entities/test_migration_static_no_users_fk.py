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

_VERSIONS_DIR = (
    Path(__file__).resolve().parents[4] / "src" / "db" / "migrations" / "versions"
)
# a0e7e1454b60가 이 파일을 교정한다 — 교정 대상 자체를 예외로 남겨 둔다.
_KNOWN_HISTORICAL_OFFENDER = "e6b1d94a7c3f_fa2_entities_hierarchy.py"

_EXECUTE_BODY = re.compile(
    r'op\.execute\(\s*f?"""(?P<triple>.*?)"""|op\.execute\(\s*f?"(?P<single>[^"]*)"',
    re.DOTALL,
)
_BAD_TENANT_FK = re.compile(
    r"tenant_id\s+UUID[^,\n]*REFERENCES\s+users", re.IGNORECASE
)


def _execute_bodies(source: str) -> list[str]:
    return [m.group("triple") or m.group("single") or "" for m in _EXECUTE_BODY.finditer(source)]


def test_no_migration_makes_legal_entity_tenant_id_reference_users() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted(_VERSIONS_DIR.glob("*.py")):
        if path.name == _KNOWN_HISTORICAL_OFFENDER:
            continue
        source = path.read_text(encoding="utf-8")
        for body in _execute_bodies(source):
            if "legal_entity" not in body:
                continue
            match = _BAD_TENANT_FK.search(body)
            if match is not None:
                offenders.append((path.name, match.group(0).strip()))

    assert offenders == [], (
        "legal_entity.tenant_id가 users(user_id)를 FK하는 마이그레이션이 "
        f"재도입됐다(ADR-2026-09-06-E FA-0a 결함 재생산): {offenders}"
    )


def test_known_historical_offender_is_the_only_allowlisted_file() -> None:
    assert (_VERSIONS_DIR / _KNOWN_HISTORICAL_OFFENDER).exists()
