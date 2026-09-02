"""PerformanceRepository의 asyncpg 구현.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.7 M5.

`components`/`returns`/`risk`/`benchmark`는 JSONB에 넣어야 하므로 Decimal을
문자열로 직렬화한다(부동소수 표현 오차로 값이 흔들리지 않게 — 이 세션
전반의 관례, reconciliation의 `compute_input_hash`와 같은 이유)."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.foundation.performance.domain.models import (
    AttributionSlice,
    ComponentBreakdown,
    Methodology,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)

_SELECT_WITH_METHODOLOGY_HASH = (
    "SELECT ps.*, pm.methodology_hash FROM performance_statement ps "
    "JOIN performance_methodology pm ON pm.version = ps.methodology_version"
)

_BREAKDOWN_FIELDS = (
    "gross_pnl",
    "fees",
    "slippage",
    "funding",
    "fx",
    "cashflows_net",
    "estimated_tax",
    "net_pnl",
)


def _decimal_or_none_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _str_to_decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _breakdown_to_json(b: ComponentBreakdown) -> str:
    return json.dumps({f: _decimal_or_none_to_str(getattr(b, f)) for f in _BREAKDOWN_FIELDS})


def _breakdown_from_json(raw: str) -> ComponentBreakdown:
    data = json.loads(raw)
    return ComponentBreakdown(**{f: _str_to_decimal_or_none(data[f]) for f in _BREAKDOWN_FIELDS})


def _returns_to_json(returns: tuple[ReturnFigure, ...]) -> str:
    return json.dumps(
        [
            {
                "value_pct": _decimal_or_none_to_str(r.value_pct),
                "basis": r.basis,
                "method": r.method,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "annualized": r.annualized,
                "periods_per_year": r.periods_per_year,
            }
            for r in returns
        ]
    )


def _returns_from_json(raw: str) -> tuple[ReturnFigure, ...]:
    return tuple(
        ReturnFigure(
            value_pct=_str_to_decimal_or_none(r["value_pct"]),
            basis=r["basis"],
            method=r["method"],
            period_start=datetime.fromisoformat(r["period_start"]),
            period_end=datetime.fromisoformat(r["period_end"]),
            annualized=r["annualized"],
            periods_per_year=r["periods_per_year"],
        )
        for r in json.loads(raw)
    )


def _decimal_dict_to_json(d: dict[str, Decimal | None] | None) -> str | None:
    if d is None:
        return None
    return json.dumps({k: _decimal_or_none_to_str(v) for k, v in d.items()})


def _decimal_dict_from_json(raw: str | None) -> dict[str, Decimal | None] | None:
    if raw is None:
        return None
    return {k: _str_to_decimal_or_none(v) for k, v in json.loads(raw).items()}


def _row_to_statement(row: asyncpg.Record) -> PerformanceStatement:
    return PerformanceStatement(
        id=row["id"],
        tenant_id=row["tenant_id"],
        scope=row["scope"],
        scope_ref=row["scope_ref"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        as_of=row["as_of"],
        methodology_version=row["methodology_version"],
        methodology_hash=row["methodology_hash"],
        input_refs=tuple(json.loads(row["input_refs"])),
        components=_breakdown_from_json(row["components"]),
        returns=_returns_from_json(row["returns"]),
        risk=_decimal_dict_from_json(row["risk"]) or {},
        benchmark=_decimal_dict_from_json(row["benchmark"]),
        benchmark_ref=row["benchmark_ref"],
        state=StatementState(row["state"]),
        revision_no=row["revision_no"],
        prior_statement_id=row["prior_statement_id"],
        identity_ok=row["identity_ok"],
        identity_residual=row["identity_residual"],
        limitations=tuple(row["limitations"]),
        evidence_refs=tuple(json.loads(row["evidence_refs"])),
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
        """WORM 대상은 아니지만(§3.7에 REVOKE 명시 안 됨) 버전 문자열이 이미
        내용 주소 성격이라(methodology_hash가 버전 정의를 완전히 결정) 실제로
        재정의할 일이 없다 — `ON CONFLICT DO NOTHING`으로 중복 삽입만 흡수."""
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
                _breakdown_to_json(statement.components),
                _returns_to_json(statement.returns),
                _decimal_dict_to_json(statement.risk) or "{}",
                _decimal_dict_to_json(statement.benchmark),
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
        assert hash_row is not None  # FK 제약이 이미 존재를 보장한다
        return _row_to_statement({**dict(row), "methodology_hash": hash_row["methodology_hash"]})

    async def get_statement(self, statement_id: UUID) -> PerformanceStatement | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_WITH_METHODOLOGY_HASH + " WHERE ps.id = $1",
                                       statement_id)
        return _row_to_statement(row) if row is not None else None

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
        return tuple(_row_to_statement(r) for r in rows)

    async def get_latest_statement(
        self,
        *,
        tenant_id: UUID,
        scope: str,
        scope_ref: str,
        period_start: datetime,
        period_end: datetime,
    ) -> PerformanceStatement | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _SELECT_WITH_METHODOLOGY_HASH + " WHERE ps.tenant_id = $1 AND ps.scope = $2 "
                "AND ps.scope_ref = $3 AND ps.period_start = $4 AND ps.period_end = $5 "
                "ORDER BY ps.revision_no DESC LIMIT 1",
                tenant_id,
                scope,
                scope_ref,
                period_start,
                period_end,
            )
        return _row_to_statement(row) if row is not None else None

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
