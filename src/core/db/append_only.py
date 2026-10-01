"""Append-only (WORM) table DDL generator.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §2.1 L0-3, §9 L0-3

Revoking UPDATE, DELETE from PUBLIC alone does not enforce WORM —
PostgreSQL always treats the table owner (the role running migrations)
as the full privileged user regardless of GRANT/REVOKE settings
(see unresolved note left in migration 9ec8a1ee28d7 docstring).
Triggers, however, fire even for the owner without exception, so a
`BEFORE UPDATE OR DELETE` trigger that always raises an exception is
the actual enforcement mechanism. REVOKE is kept as a defensive
hardening measure for PUBLIC/non-owner roles.

This module only generates SQL strings — execution (migration apply)
belongs to L0-5.
"""

from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class InvalidIdentifierError(ValueError):
    """Table name does not match safe SQL identifier syntax (injection prevention)."""


def _validate_table(table: str) -> None:
    if not _IDENTIFIER_RE.match(table):
        raise InvalidIdentifierError(f"테이블 이름이 안전한 식별자가 아닙니다: {table!r}")


def _guard_function_name(table: str) -> str:
    return f"{table}_worm_guard"


def _guard_trigger_name(table: str) -> str:
    return f"{table}_worm_guard_trg"


def worm_sql(table: str) -> list[str]:
    """Return a list of DDL statements that make `table` append-only (WORM).

    Order: REVOKE (defensive hardening) → create guard function → attach guard trigger.
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
    """Return a list of DDL statements that reverse the enforcement applied by `worm_sql(table)`."""
    _validate_table(table)
    guard_fn = _guard_function_name(table)
    trigger = _guard_trigger_name(table)
    return [
        f"DROP TRIGGER IF EXISTS {trigger} ON {table}",
        f"DROP FUNCTION IF EXISTS {guard_fn}()",
        f"GRANT UPDATE, DELETE ON {table} TO PUBLIC",
    ]
