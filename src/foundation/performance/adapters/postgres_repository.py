"""asyncpg implementation of PerformanceRepository.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.7 M5.

Decimal↔JSONB (de)serialization of statement rows was extracted into
postgres_statement_repository.py (P6: split at 300+ lines) — this file
is responsible only for query execution and methodology/attribution mapping."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.foundation.performance.adapters.postgres_statement_repository import (
    breakdown_to_json,
    decimal_dict_to_json,
    returns_to_json,
    row_to_statement,
)
from src.foundation.performance.domain.models import (
    AttributionSlice,
    Methodology,
    PerformanceStatement,
)

_SELECT_WITH_METHODOLOGY_HASH = (
    "SELECT ps.*, pm.methodology_hash FROM performance_statement ps "
    "JOIN performance_methodology pm ON pm.version = ps.methodology_version"
)


def _row_to_methodology(row: asyncpg.Record) -> Methodology:
    definition = json.loads(row["definition"])
    return Methodology(
        version=row["version"],
        methodology_hash=row["methodology_hash"],
        twr_method=definition["twr_method"],
        mwr_method=definition["mwr_method"],
        risk_free_rate_pct=Decimal(definition["risk_free_rate_pct"]),
        periods_per_year=definition["periods_per_year"],
    )


def _row_to_attribution(row: asyncpg.Record) -> AttributionSlice:
    return AttributionSlice(
        statement_id=row["statement_id"],
        dimension=row["dimension"],
        key=row["key"],
        contribution=row["contribution"],
        confidence=row["confidence"],
        limitation=row["limitation"],
    )


class PostgresPerformanceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_methodology(self, version: str) -> Methodology | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM performance_methodology WHERE version = $1", version
            )
        return _row_to_methodology(row) if row is not None else None

    async def insert_methodology(self, methodology: Methodology) -> Methodology:
        """Not a WORM target (no REVOKE specified in §3.7), but the version string
        carries content-addressed semantics (methodology_hash fully defines the version),
        so overrides never occur in practice — `ON CONFLICT DO NOTHING` simply absorbs
        duplicate inserts."""
        definition = json.dumps(
            {
                "twr_method": methodology.twr_method,
                "mwr_method": methodology.mwr_method,
                "risk_free_rate_pct": str(methodology.risk_free_rate_pct),
                "periods_per_year": methodology.periods_per_year,
            }
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO performance_methodology (version, methodology_hash, definition) "
                "VALUES ($1, $2, $3) ON CONFLICT (version) DO NOTHING RETURNING *",
                methodology.version,
                methodology.methodology_hash,
                definition,
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM performance_methodology WHERE version = $1",
                    methodology.version,
                )
                assert row is not None
        return _row_to_methodology(row)

    async def insert_statement(self, statement: PerformanceStatement) -> PerformanceStatement:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO performance_statement "
                "(tenant_id, scope, scope_ref, period_start, period_end, as_of, "
                " methodology_version, input_refs, components, returns, risk, benchmark, "
                " benchmark_ref, state, revision_no, prior_statement_id, identity_ok, "
                " identity_residual, limitations, evidence_refs) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, "
                "        $16, $17, $18, $19, $20) "
                "RETURNING *",
                statement.tenant_id,
                statement.scope,
                statement.scope_ref,
                statement.period_start,
                statement.period_end,
                statement.as_of,
                statement.methodology_version,
                json.dumps(list(statement.input_refs)),
                breakdown_to_json(statement.components),
                returns_to_json(statement.returns),
                decimal_dict_to_json(statement.risk) or "{}",
                decimal_dict_to_json(statement.benchmark),
                statement.benchmark_ref,
                statement.state.value,
                statement.revision_no,
                statement.prior_statement_id,
                statement.identity_ok,
                statement.identity_residual,
                list(statement.limitations),
                json.dumps(list(statement.evidence_refs)),
            )
            hash_row = await conn.fetchrow(
                "SELECT methodology_hash FROM performance_methodology WHERE version = $1",
                statement.methodology_version,
            )
        assert hash_row is not None  # FK constraint guarantees existence
        return row_to_statement({**dict(row), "methodology_hash": hash_row["methodology_hash"]})

    async def get_statement(self, statement_id: UUID) -> PerformanceStatement | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_WITH_METHODOLOGY_HASH + " WHERE ps.id = $1", statement_id
            )
        return row_to_statement(row) if row is not None else None

    async def list_statements(
        self, *, tenant_id: UUID, scope: str | None = None
    ) -> tuple[PerformanceStatement, ...]:
        async with self._pool.acquire() as conn:
            if scope is None:
                rows = await conn.fetch(
                    _SELECT_WITH_METHODOLOGY_HASH + " WHERE ps.tenant_id = $1 "
                    "ORDER BY ps.period_end DESC, ps.revision_no DESC",
                    tenant_id,
                )
            else:
                rows = await conn.fetch(
                    _SELECT_WITH_METHODOLOGY_HASH + " WHERE ps.tenant_id = $1 AND ps.scope = $2 "
                    "ORDER BY ps.period_end DESC, ps.revision_no DESC",
                    tenant_id,
                    scope,
                )
        return tuple(row_to_statement(r) for r in rows)

    async def get_latest_statement(
        self,
        *,
        tenant_id: UUID,
        scope: str,
        scope_ref: str,
        period_start: datetime,
        period_end: datetime,
        methodology_version: str,
    ) -> PerformanceStatement | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_WITH_METHODOLOGY_HASH + " WHERE ps.tenant_id = $1 AND ps.scope = $2 "
                "AND ps.scope_ref = $3 AND ps.period_start = $4 AND ps.period_end = $5 "
                "AND ps.methodology_version = $6 "
                "ORDER BY ps.revision_no DESC LIMIT 1",
                tenant_id,
                scope,
                scope_ref,
                period_start,
                period_end,
                methodology_version,
            )
        return row_to_statement(row) if row is not None else None

    async def insert_attribution(self, slice_: AttributionSlice) -> AttributionSlice:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO performance_attribution_slice "
                "(statement_id, dimension, key, contribution, confidence, limitation) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                slice_.statement_id,
                slice_.dimension,
                slice_.key,
                slice_.contribution,
                slice_.confidence,
                slice_.limitation,
            )
        return _row_to_attribution(row)

    async def list_attribution(self, statement_id: UUID) -> tuple[AttributionSlice, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM performance_attribution_slice WHERE statement_id = $1",
                statement_id,
            )
        return tuple(_row_to_attribution(r) for r in rows)
