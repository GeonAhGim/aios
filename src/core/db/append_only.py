"""append-only(WORM) 테이블 공통 DDL 생성기.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §2.1 L0-3, §9 L0-3

`REVOKE UPDATE, DELETE ... FROM PUBLIC`만으로는 WORM이 강제되지 않는다 —
PostgreSQL은 테이블 소유자(마이그레이션을 실행하는 역할)를 GRANT/REVOKE와
무관하게 항상 전체 권한자로 취급한다(마이그레이션 9ec8a1ee28d7 docstring이
남긴 미해결 문제). 반면 트리거는 소유자에게도 예외 없이 발동하므로,
`BEFORE UPDATE OR DELETE` 트리거가 항상 `RAISE EXCEPTION`하는 것이 실제
강제 수단이다. REVOKE는 PUBLIC/비소유 역할에 대한 방어 심화로 함께 둔다.

이 모듈은 SQL 문자열만 생성한다 — 실행(마이그레이션 적용)은 L0-5.
"""
from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class InvalidIdentifierError(ValueError):
    """테이블명이 안전한 SQL 식별자 형식이 아니다(인젝션 방지)."""


def _validate_table(table: str) -> None:
    if not _IDENTIFIER_RE.match(table):
        raise InvalidIdentifierError(f"테이블 이름이 안전한 식별자가 아닙니다: {table!r}")


def _guard_function_name(table: str) -> str:
    return f"{table}_worm_guard"


def _guard_trigger_name(table: str) -> str:
    return f"{table}_worm_guard_trg"


def worm_sql(table: str) -> list[str]:
    """`table`을 append-only(WORM)로 만드는 DDL 문 목록을 반환한다.

    순서: REVOKE(방어 심화) → 가드 함수 생성 → 가드 트리거 부착.
    """
    _validate_table(table)
    guard_fn = _guard_function_name(table)
    trigger = _guard_trigger_name(table)
    return [
        f"REVOKE UPDATE, DELETE ON {table} FROM PUBLIC",
        (
            f"CREATE OR REPLACE FUNCTION {guard_fn}() RETURNS trigger AS $$\n"
            "BEGIN\n"
            f"    RAISE EXCEPTION 'append-only violation: % on {table} denied', TG_OP;\n"
            "END;\n"
            "$$ LANGUAGE plpgsql"
        ),
        (
            f"CREATE TRIGGER {trigger}\n"
            f"    BEFORE UPDATE OR DELETE ON {table}\n"
            f"    FOR EACH ROW EXECUTE FUNCTION {guard_fn}()"
        ),
    ]


def worm_drop_sql(table: str) -> list[str]:
    """`worm_sql(table)`이 만든 강제를 역순으로 해제하는 DDL 문 목록을 반환한다."""
    _validate_table(table)
    guard_fn = _guard_function_name(table)
    trigger = _guard_trigger_name(table)
    return [
        f"DROP TRIGGER IF EXISTS {trigger} ON {table}",
        f"DROP FUNCTION IF EXISTS {guard_fn}()",
        f"GRANT UPDATE, DELETE ON {table} TO PUBLIC",
    ]
