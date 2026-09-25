"""AI-19 -- `ml_model_registry` Postgres adapter.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-19
("postgres_model_registry" + migration, "lineage storage").

`register()` is idempotent (standard-105): `INSERT ... ON CONFLICT
(model_id, version) DO NOTHING` -- if no row was inserted, the existing row
is re-fetched and handed to `domain/registry_rules.py::
validate_new_registration`, which raises `ModelHashMismatchError` unless
the hash matches (a matching hash means this is a safe retry of an
already-applied registration, so the existing row is returned rather than
erroring).
"""

from __future__ import annotations

import json

import asyncpg

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.registry_rules import validate_new_registration

__all__ = ["PostgresModelRegistry"]


def _row_to_model_card(row: asyncpg.Record) -> ModelCard:
    return ModelCard(
        model_id=row["model_id"],
        version=row["version"],
        model_hash=row["model_hash"],
        train_data_lineage=TrainDataLineage(
            start=row["train_lineage_start"],
            end=row["train_lineage_end"],
            source_ref=row["train_lineage_source_ref"],
        ),
        trained_at=row["trained_at"],
        metrics=json.loads(row["metrics"]),
        drift_baseline={k: tuple(v) for k, v in json.loads(row["drift_baseline"]).items()},
    )


class PostgresModelRegistry:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def register(self, card: ModelCard) -> ModelCard:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO ml_model_registry "
                "(model_id, version, model_hash, train_lineage_start, train_lineage_end, "
                " train_lineage_source_ref, trained_at, metrics, drift_baseline) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb) "
                "ON CONFLICT (model_id, version) DO NOTHING RETURNING *",
                card.model_id,
                card.version,
                card.model_hash,
                card.train_data_lineage.start,
                card.train_data_lineage.end,
                card.train_data_lineage.source_ref,
                card.trained_at,
                json.dumps(card.metrics),
                json.dumps(card.drift_baseline),
            )
            if row is not None:
                return _row_to_model_card(row)

            existing_row = await conn.fetchrow(
                "SELECT * FROM ml_model_registry WHERE model_id = $1 AND version = $2",
                card.model_id,
                card.version,
            )
        assert existing_row is not None  # ON CONFLICT fired, so a row must exist
        existing = _row_to_model_card(existing_row)
        validate_new_registration(card, existing=existing)
        return existing

    async def get(self, model_id: str, version: str) -> ModelCard | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ml_model_registry WHERE model_id = $1 AND version = $2",
                model_id,
                version,
            )
        return None if row is None else _row_to_model_card(row)

    async def latest(self, model_id: str) -> ModelCard | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ml_model_registry WHERE model_id = $1 "
                "ORDER BY trained_at DESC, registered_at DESC LIMIT 1",
                model_id,
            )
        return None if row is None else _row_to_model_card(row)
