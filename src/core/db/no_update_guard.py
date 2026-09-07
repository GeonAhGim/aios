"""No-UPDATE guard DDL generator for mutable projection tables (FA-10).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10
(SS2.3, SS4 FA-A2).

Unlike `src/core/db/append_only.py`'s WORM guard (blocks UPDATE *and* DELETE
for genuinely append-only ledgers), the three FA-10 projection tables
(`pos_snapshot`, `ledger_balance`, legacy `positions`) are still mutable
*state* -- they are simply no longer mutable *in place*. A write now
replaces the current row with DELETE + INSERT (same natural key, new
`tx_from`) instead of UPDATE (FA-A2: "correction is a new row"). DELETE
stays legal so that replacement pattern keeps working -- only UPDATE is
physically impossible, enforced the same way `append_only.py` enforces WORM:
a trigger that fires unconditionally, including for the table owner (REVOKE
alone never binds an owning role -- only a trigger does).
"""
from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class InvalidIdentifierError(ValueError):
    """Table name is not a safe SQL identifier (injection guard)."""


def _validate_table(table: str) -> None:
    if not _IDENTIFIER_RE.match(table):
        raise InvalidIdentifierError(f"table name is not a safe identifier: {table!r}")


def _guard_function_name(table: str) -> str:
    return f"{table}_no_update_guard"


def _guard_trigger_name(table: str) -> str:
    return f"{table}_no_update_guard_trg"


def no_update_guard_sql(table: str) -> list[str]:
    """DDL statements that make UPDATE on `table` physically impossible.

    Order: REVOKE (defense in depth) -> create guard function -> attach
    guard trigger.
    """
    _validate_table(table)
    guard_fn = _guard_function_name(table)
    trigger = _guard_trigger_name(table)
    return [
        f"REVOKE UPDATE ON {table} FROM PUBLIC",
        (
            f"CREATE OR REPLACE FUNCTION {guard_fn}() RETURNS trigger AS $$\n"
            "BEGIN\n"
            f"    RAISE EXCEPTION 'no-update violation: UPDATE on {table} denied "
            "(FA-A2 -- insert a replacement row instead)';\n"
            "END;\n"
            "$$ LANGUAGE plpgsql"
        ),
        (
            f"CREATE TRIGGER {trigger}\n"
            f"    BEFORE UPDATE ON {table}\n"
            f"    FOR EACH ROW EXECUTE FUNCTION {guard_fn}()"
        ),
    ]


def no_update_guard_drop_sql(table: str) -> list[str]:
    """Reverses, in order, whatever `no_update_guard_sql(table)` applied."""
    _validate_table(table)
    guard_fn = _guard_function_name(table)
    trigger = _guard_trigger_name(table)
    return [
        f"DROP TRIGGER IF EXISTS {trigger} ON {table}",
        f"DROP FUNCTION IF EXISTS {guard_fn}()",
        f"GRANT UPDATE ON {table} TO PUBLIC",
    ]
