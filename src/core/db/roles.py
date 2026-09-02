"""DB 역할 분리 SQL 생성기 — 소유자(migrator) vs 애플리케이션(app).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §2.1 L0-3, §9 L0-3

`aios_migrator`는 마이그레이션을 실행하는 테이블 소유자 역할, `aios_app`은
런타임 애플리케이션이 접속하는 DML 전용 역할이다. append-only 테이블의
실제 WORM 강제는 [[append_only.worm_sql]]의 트리거가 담당한다 — 이 모듈은
그 트리거가 실효를 가지려면 애플리케이션이 소유자가 아닌 별도 역할로
접속해야 한다는 전제(9ec8a1ee28d7 docstring)를 채운다.

PostgreSQL은 `CREATE ROLE IF NOT EXISTS`를 지원하지 않으므로 `DO` 블록으로
존재 여부를 확인한다. 이 모듈은 SQL 문자열만 생성한다 — 실행은 L0-5.
"""
from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class InvalidIdentifierError(ValueError):
    """역할명이 안전한 SQL 식별자 형식이 아니거나 서로 겹친다(인젝션 방지)."""


def _validate_role(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise InvalidIdentifierError(f"역할 이름이 안전한 식별자가 아닙니다: {name!r}")


def _ensure_role_sql(role: str, *, login: bool) -> str:
    option = "LOGIN" if login else "NOLOGIN"
    return (
        "DO $$\n"
        "BEGIN\n"
        f"    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{role}') THEN\n"
        f"        CREATE ROLE {role} {option};\n"
        "    END IF;\n"
        "END\n"
        "$$"
    )


def ensure_roles_sql(app_role: str, migrator_role: str) -> list[str]:
    """`migrator_role`(소유자)·`app_role`(DML 전용) 생성 + 기본 권한 부여 SQL 목록.

    `app_role`은 스키마 내 테이블에 SELECT/INSERT/UPDATE/DELETE를 받는다 —
    append-only 테이블에 한해 UPDATE/DELETE를 막는 것은 `worm_sql`의
    트리거·REVOKE가 테이블 단위로 별도로 담당한다(이 함수는 스키마 전역
    기본값만 다룬다).
    """
    _validate_role(app_role)
    _validate_role(migrator_role)
    if app_role == migrator_role:
        raise InvalidIdentifierError("app_role과 migrator_role은 같을 수 없습니다")
    return [
        _ensure_role_sql(migrator_role, login=True),
        _ensure_role_sql(app_role, login=True),
        f"GRANT ALL PRIVILEGES ON SCHEMA public TO {migrator_role}",
        f"GRANT USAGE ON SCHEMA public TO {app_role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}",
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_role} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_role}"
        ),
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_role} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {app_role}"
        ),
    ]
