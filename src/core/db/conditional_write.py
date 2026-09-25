"""Shared helper for concurrency/atomicity standard.

Spec: AIOSproject 105_concurrency_and_atomicity_engineering_standard_v1.0.md

Nineteen services (dispute_resolution_service, portfolio_service, verification_service,
strategy_builder_service, wallet_service, etc.) each hand-implemented the same pattern:
"read and validate the state, then re-apply it as an UPDATE condition and confirm via
RETURNING". This helper consolidates that pattern in one place — it does not force
migration of existing services (their behavior is already correct), but new bounded
contexts created after FND-01 (src/foundation/) must perform conditional writes only
through this helper.
"""
from __future__ import annotations

from typing import Any

import asyncpg


class ConcurrencyConflictError(Exception):
    """The actual state at write time differed from the state we read.

    The caller must re-query and retry, or expose a 409 to the user — do not
    swallow this exception.
    """


async def conditional_update(
    conn: asyncpg.Connection,
    *,
    table: str,
    id_column: str,
    id_value: Any,
    expected_state_column: str,
    expected_state_value: Any,
    set_values: dict[str, Any],
    returning: str = "*",
    extra_conditions: dict[str, Any] | None = None,
) -> asyncpg.Record:
    """Conditional UPDATE with
    `WHERE <id_column> = $1 AND <expected_state_column> IS NOT DISTINCT FROM $2`,
    raising ConcurrencyConflictError if RETURNING yields an empty result.

    Why `IS NOT DISTINCT FROM` (instead of `=`) — transitions that expect "no value yet"
    (NULL), such as FND-02 activate_revision(), do exist (initial activation, etc.).
    Plain `=` treats `NULL = NULL` as NULL (false), causing a constant mismatch in these
    cases and forcing callers to bypass this helper whenever they handle NULL.
    `IS NOT DISTINCT FROM` behaves intuitively for both NULL and non-NULL, preventing
    that bypass.

    The **keys** (column names) of `table`/`id_column`/`expected_state_column`/`returning`
    and `set_values`/`extra_conditions` must be hardcoded in the caller's code
    (never pass user input directly). Values are bound as positional parameters by this
    function, so the caller never needs to count `$N` numbers themselves — this prevents
    errors from miscounting column order.

    `extra_conditions` (default None, no impact on existing callers) — additional
    (column->expected_value) pairs to append to the WHERE clause beyond the main condition
    (`expected_state_column`). Used when the L4-07 `orders` transition needs to assert
    both `status` match and `version = $expected_version` (optimistic lock, I5) in a
    single UPDATE statement — if RETURNING returns 0 rows without a separate SELECT,
    it means either the state or the version has drifted.
    """
    extra_items = list((extra_conditions or {}).items())
    base_params = [id_value, expected_state_value, *(v for _, v in extra_items)]
    extra_clause = "".join(
        f" AND {col} IS NOT DISTINCT FROM ${i + 3}" for i, (col, _) in enumerate(extra_items)
    )

    set_columns = list(set_values.keys())
    set_start = len(base_params) + 1
    set_clause = ", ".join(f"{col} = ${set_start + i}" for i, col in enumerate(set_columns))
    sql = (
        f"UPDATE {table} SET {set_clause} "  # noqa: S608 — column names are caller constants (see docstring)
        f"WHERE {id_column} = $1 AND {expected_state_column} IS NOT DISTINCT FROM $2"
        f"{extra_clause} "
        f"RETURNING {returning}"
    )
    params = [*base_params, *(set_values[col] for col in set_columns)]
    row = await conn.fetchrow(sql, *params)
    if row is None:
        raise ConcurrencyConflictError(
            f"{table}.{id_column}={id_value}: another request was processed first"
            "(concurrency conflict) — please re-query and try again."
        )
    return row
