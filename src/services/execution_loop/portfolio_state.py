"""L23 -- persists `PortfolioConfig` (L17) into
`strategy_executions.portfolio_config` (migration M2, this leaf's other
half).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L23.

`save_config` folds two guards into one round-trip UPDATE:

- I11's conditional UPDATE, keyed on `portfolio_config_version` (same
  optimistic-lock shape as `strategy_state_store.save_state`, M1).
- The L23 DoD's business rule: a running execution's config may not be
  replaced out from under it (`status <> 'RUNNING'`) -- config changes only
  take effect while the execution is not actively trading.

Both guards live in the same `WHERE` clause, so a successful save is a
single round trip. When `RETURNING` comes back empty, the two causes need
different exceptions (a version conflict is retryable by the caller; a
RUNNING execution is not, regardless of version), so the failure path does
one extra `SELECT` to tell them apart.
"""

from __future__ import annotations

import json

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.portfolio.config import PortfolioConfig

_UPDATE_SQL = """
UPDATE strategy_executions
SET portfolio_config = $2::jsonb,
    portfolio_config_version = portfolio_config_version + 1
WHERE id = $1
    AND portfolio_config_version = $3
    AND status <> 'RUNNING'
RETURNING id, portfolio_config, portfolio_config_version
"""

_STATUS_SQL = "SELECT status, portfolio_config_version FROM strategy_executions WHERE id = $1"

_SELECT_SQL = """
SELECT portfolio_config, portfolio_config_version
FROM strategy_executions
WHERE id = $1
"""


class PortfolioConfigLockedError(Exception):
    """The execution's `status` is `RUNNING` -- `portfolio_config` may only
    be replaced while the execution is not actively trading (§9 L23 DoD).
    Not retryable by re-sending the same write; the caller must pause or
    retire the execution first."""


def _encode(config: PortfolioConfig) -> str:
    return json.dumps(config.model_dump(mode="json"))


def _decode(payload: str) -> PortfolioConfig:
    return PortfolioConfig.model_validate(json.loads(payload))


async def load_config(conn: asyncpg.Connection, execution_id: int) -> PortfolioConfig | None:
    row = await conn.fetchrow(_SELECT_SQL, execution_id)
    if row is None:
        raise LookupError(f"strategy_executions.id={execution_id}: 존재하지 않는 실행입니다")
    if row["portfolio_config"] is None:
        return None
    return _decode(row["portfolio_config"])


async def save_config(
    conn: asyncpg.Connection,
    execution_id: int,
    config: PortfolioConfig,
    *,
    expected_version: int,
) -> PortfolioConfig:
    """Raises `PortfolioConfigLockedError` if the execution is `RUNNING`,
    `ConcurrencyConflictError` if another writer already advanced
    `portfolio_config_version` past `expected_version`. Both are surfaced
    to the caller unmodified (fail-closed) -- neither is retried here."""
    row = await conn.fetchrow(
        _UPDATE_SQL,
        execution_id,
        _encode(config),
        expected_version,
    )
    if row is not None:
        return _decode(row["portfolio_config"])

    status_row = await conn.fetchrow(_STATUS_SQL, execution_id)
    if status_row is None:
        raise LookupError(f"strategy_executions.id={execution_id}: 존재하지 않는 실행입니다")
    if status_row["status"] == "RUNNING":
        raise PortfolioConfigLockedError(
            f"strategy_executions.id={execution_id}: RUNNING 중에는 "
            "portfolio_config을 변경할 수 없습니다 -- 먼저 PAUSED/RETIRED로 전환하세요."
        )
    raise ConcurrencyConflictError(
        f"strategy_executions.id={execution_id}: portfolio_config_version이 "
        f"기대값({expected_version})과 다릅니다(동시 갱신 충돌) -- "
        "load_config으로 다시 조회 후 재시도하세요."
    )


__all__ = ["PortfolioConfigLockedError", "load_config", "save_config"]
