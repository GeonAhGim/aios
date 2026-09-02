"""ValidationRepository의 asyncpg 구현.

Spec: AIOSproject 76번 §1/§2, 105번(동시성 표준).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.foundation.validation.domain.models import (
    Outcome,
    RunState,
    ValidationResult,
    ValidationRun,
)


def _row_to_run(row: asyncpg.Record) -> ValidationRun:
    return ValidationRun(
        id=row["id"],
        strategy_id=row["strategy_id"],
        strategy_version=row["strategy_version"],
        check_type=row["check_type"],
        input_snapshot_hash=row["input_snapshot_hash"],
        cost_model=json.loads(row["cost_model"]),
        warmup_bars=row["warmup_bars"],
        periods_per_year=row["periods_per_year"],
        initial_equity=row["initial_equity"],
        state=RunState(row["state"]),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _row_to_result(row: asyncpg.Record) -> ValidationResult:
    return ValidationResult(
        id=row["id"],
        run_id=row["run_id"],
        outcome=Outcome(row["outcome"]),
        metrics=json.loads(row["metrics"]),
        warnings=tuple(row["warnings"]),
        hard_fail_reasons=tuple(row["hard_fail_reasons"]),
        obligations=tuple(row["obligations"]),
        result_hash=row["result_hash"],
        created_at=row["created_at"],
    )


class PostgresValidationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_run_by_snapshot(
        self, strategy_id: str, strategy_version: str, check_type: str, input_snapshot_hash: str
    ) -> ValidationRun | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM strategy_validation_run "
                "WHERE strategy_id = $1 AND strategy_version = $2 AND check_type = $3 "
                "AND input_snapshot_hash = $4",
                strategy_id,
                strategy_version,
                check_type,
                input_snapshot_hash,
            )
        return _row_to_run(row) if row is not None else None

    async def create_run(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        check_type: str,
        input_snapshot_hash: str,
        cost_model: dict[str, Any],
        warmup_bars: int,
        periods_per_year: int,
        initial_equity: Decimal,
    ) -> ValidationRun:
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO strategy_validation_run "
                    "(strategy_id, strategy_version, check_type, input_snapshot_hash, "
                    " cost_model, warmup_bars, periods_per_year, initial_equity) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8) RETURNING *",
                    strategy_id,
                    strategy_version,
                    check_type,
                    input_snapshot_hash,
                    json.dumps(cost_model),
                    warmup_bars,
                    periods_per_year,
                    initial_equity,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConcurrencyConflictError(
                    f"strategy_validation_run: {strategy_id}/{strategy_version}/{check_type}에 "
                    "대한 이 정확한 입력 조합은 이미 다른 요청이 먼저 만들었습니다."
                ) from exc
        return _row_to_run(row)

    async def mark_running(self, run_id: UUID) -> ValidationRun:
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table="strategy_validation_run",
                id_column="id",
                id_value=run_id,
                expected_state_column="state",
                expected_state_value=RunState.QUEUED.value,
                set_values={"state": RunState.RUNNING.value},
            )
        return _row_to_run(row)

    async def mark_failed(self, run_id: UUID) -> ValidationRun:
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table="strategy_validation_run",
                id_column="id",
                id_value=run_id,
                expected_state_column="state",
                expected_state_value=RunState.RUNNING.value,
                set_values={
                    "state": RunState.FAILED.value,
                    "completed_at": datetime.now(timezone.utc),
                },
            )
        return _row_to_run(row)

    async def complete_with_result(
        self, run_id: UUID, result: ValidationResult
    ) -> tuple[ValidationRun, ValidationResult]:
        async with self._pool.acquire() as conn, conn.transaction():
            run_row = await conditional_update(
                conn,
                table="strategy_validation_run",
                id_column="id",
                id_value=run_id,
                expected_state_column="state",
                expected_state_value=RunState.RUNNING.value,
                set_values={
                    "state": RunState.SUCCEEDED.value,
                    "completed_at": datetime.now(timezone.utc),
                },
            )
            result_row = await conn.fetchrow(
                "INSERT INTO strategy_validation_result "
                "(id, run_id, outcome, metrics, warnings, hard_fail_reasons, obligations, "
                " result_hash) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8) RETURNING *",
                uuid4(),
                run_id,
                result.outcome.value,
                json.dumps(result.metrics),
                list(result.warnings),
                list(result.hard_fail_reasons),
                list(result.obligations),
                result.result_hash,
            )
        return _row_to_run(run_row), _row_to_result(result_row)

    async def get_result_for_run(self, run_id: UUID) -> ValidationResult | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM strategy_validation_result WHERE run_id = $1", run_id
            )
        return _row_to_result(row) if row is not None else None
