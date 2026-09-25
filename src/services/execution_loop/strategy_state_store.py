"""L13 -- persists `StrategyStateMemory` (L08) into `strategy_execution_state`
(migration M1, this leaf's other half).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §3.7 M1, §9 L13.

`save_state` is a single-round-trip `INSERT ... ON CONFLICT (execution_id)
DO UPDATE ... WHERE state_version IS NOT DISTINCT FROM $expected`. This
folds two cases the caller would otherwise need to branch on into one
statement: an execution_id's *first* save (no row yet -- `INSERT` always
proceeds, `expected_version` irrelevant) and a *later* save (I11's
conditional UPDATE, keyed on the `state_version` this call's `memory` was
advanced from). Two concurrent first-saves for the same execution_id both
attempt the plain `INSERT`; the loser hits `ON CONFLICT`, falls into
`DO UPDATE ... WHERE state_version IS NOT DISTINCT FROM NULL` against the
winner's already-committed `state_version=0` row, that `IS NOT DISTINCT
FROM` comparison fails (0 is distinct from NULL), so the `WHERE` filters
the row out of `DO UPDATE`'s target and `RETURNING` comes back empty --
same "no row returned" shape a genuine stale-`state_version` conflict
produces, so `save_state` raises `ConcurrencyConflictError` for both.
"""

from __future__ import annotations

import json

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.strategy.state_memory import StrategyStateMemory

_UPSERT_SQL = """
INSERT INTO strategy_execution_state
    (execution_id, state, state_version, schema_version, updated_at)
VALUES ($1, $2::jsonb, $3, $4, now())
ON CONFLICT (execution_id) DO UPDATE
    SET state = EXCLUDED.state,
        state_version = EXCLUDED.state_version,
        schema_version = EXCLUDED.schema_version,
        updated_at = now()
    WHERE strategy_execution_state.state_version IS NOT DISTINCT FROM $5
RETURNING execution_id, state, state_version, schema_version
"""

_SELECT_SQL = """
SELECT execution_id, state, state_version, schema_version
FROM strategy_execution_state
WHERE execution_id = $1
"""


def _encode(memory: StrategyStateMemory) -> str:
    return json.dumps(
        {
            "last_bar_time": {tf: ts.isoformat() for tf, ts in memory.last_bar_time.items()},
            "prev_values": {key: str(val) for key, val in memory.prev_values.items()},
        }
    )


def _decode(row: asyncpg.Record) -> StrategyStateMemory:
    payload = json.loads(row["state"])
    return StrategyStateMemory.model_validate(
        {
            "schema_version": row["schema_version"],
            "execution_id": row["execution_id"],
            "state_version": row["state_version"],
            "last_bar_time": payload.get("last_bar_time", {}),
            "prev_values": payload.get("prev_values", {}),
        }
    )


async def load_state(conn: asyncpg.Connection, execution_id: int) -> StrategyStateMemory | None:
    row = await conn.fetchrow(_SELECT_SQL, execution_id)
    return None if row is None else _decode(row)


async def save_state(
    conn: asyncpg.Connection,
    memory: StrategyStateMemory,
    *,
    expected_version: int | None,
) -> StrategyStateMemory:
    """`expected_version` is the `state_version` this `memory` was advanced
    from -- `None` for an `execution_id`'s first save (I11). Raises
    `ConcurrencyConflictError` if another writer already created or
    advanced the row past that point; the caller reloads via `load_state`
    and either retries or discards this tick's result, per I11."""
    row = await conn.fetchrow(
        _UPSERT_SQL,
        memory.execution_id,
        _encode(memory),
        memory.state_version,
        memory.schema_version,
        expected_version,
    )
    if row is None:
        raise ConcurrencyConflictError(
            f"strategy_execution_state.execution_id={memory.execution_id}: "
            f"state_version이 기대값({expected_version})과 다릅니다(동시 갱신 충돌) -- "
            "load_state로 다시 조회 후 재시도하세요."
        )
    return _decode(row)


__all__ = ["load_state", "save_state"]
