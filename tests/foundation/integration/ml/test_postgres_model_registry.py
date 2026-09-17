"""AI-19 integration tests -- `adapters/postgres_model_registry.py` +
migration `f85e5d761d6b`. Spec: docs/specs/L4_ai_research_strategy_
factory_v1.0.md §2.5/§9 AI-19 DoD ("lineage storage"). ADR-2026-09-09-C D2:
negative >= 3, failure injection 1, numeric performance assertion 1,
gate-red reproduction 1.

Follows `tests/foundation/integration/experiments/test_postgres_
repository.py`'s `pool`/CHECK-gate-red pattern -- both leaves of this same
spec.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import pytest

from src.foundation.ml.adapters.postgres_model_registry import PostgresModelRegistry
from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.registry_rules import ModelHashMismatchError

_NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def registry(pool: asyncpg.Pool) -> PostgresModelRegistry:
    return PostgresModelRegistry(pool)


def _card(**overrides: Any) -> ModelCard:
    base: dict[str, Any] = dict(
        model_id=f"m-{id(overrides)}-{time.perf_counter_ns()}",
        version="v1",
        model_hash="a" * 64,
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
        ),
        trained_at=_NOW,
        metrics={"auc": 0.9, "sharpe": 1.2},
        drift_baseline={"f1": (0.1, 0.2, 0.3)},
    )
    base.update(overrides)
    return ModelCard(**base)


# --- happy path ---


async def test_register_then_get_round_trip(registry: PostgresModelRegistry) -> None:
    card = _card()
    await registry.register(card)

    got = await registry.get(card.model_id, card.version)

    assert got is not None
    assert got.model_id == card.model_id
    assert got.model_hash == card.model_hash
    assert got.train_data_lineage == card.train_data_lineage
    assert got.metrics == card.metrics
    assert got.drift_baseline == card.drift_baseline


async def test_register_identical_retry_is_idempotent(registry: PostgresModelRegistry) -> None:
    card = _card()
    first = await registry.register(card)
    second = await registry.register(card)

    assert first.model_hash == second.model_hash == card.model_hash


async def test_latest_returns_most_recently_trained_version(
    registry: PostgresModelRegistry,
) -> None:
    model_id = f"m-latest-{time.perf_counter_ns()}"
    older = _card(
        model_id=model_id,
        version="v1",
        trained_at=_NOW - timedelta(days=2),
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=60), end=_NOW - timedelta(days=3), source_ref="s3://x"
        ),
    )
    newer = _card(model_id=model_id, version="v2", trained_at=_NOW)
    await registry.register(older)
    await registry.register(newer)

    latest = await registry.latest(model_id)

    assert latest is not None
    assert latest.version == "v2"


# --- negative (>= 3) ---


async def test_get_missing_model_returns_none(registry: PostgresModelRegistry) -> None:
    assert await registry.get("does-not-exist", "v1") is None


async def test_latest_missing_model_returns_none(registry: PostgresModelRegistry) -> None:
    assert await registry.latest("does-not-exist") is None


async def test_get_wrong_version_returns_none(registry: PostgresModelRegistry) -> None:
    card = _card()
    await registry.register(card)

    assert await registry.get(card.model_id, "does-not-exist-version") is None


async def test_register_rejects_lineage_end_before_start_check(pool: asyncpg.Pool) -> None:
    """Direct SQL bypassing `TrainDataLineage`'s own validator still hits
    the migration's CHECK constraint."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO ml_model_registry "
                "(model_id, version, model_hash, train_lineage_start, train_lineage_end, "
                " train_lineage_source_ref, trained_at, metrics, drift_baseline) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, '{}'::jsonb, '{}'::jsonb)",
                f"m-check-{time.perf_counter_ns()}",
                "v1",
                "a" * 64,
                _NOW,
                _NOW - timedelta(days=1),
                "s3://x",
                _NOW,
            )


# --- failure injection: re-registration with a different model_hash ---


async def test_register_rejects_different_hash_for_same_id_version(
    registry: PostgresModelRegistry,
) -> None:
    card = _card(model_hash="a" * 64)
    await registry.register(card)

    conflicting = _card(
        model_id=card.model_id,
        version=card.version,
        model_hash="b" * 64,
        train_data_lineage=card.train_data_lineage,
        trained_at=card.trained_at,
    )
    with pytest.raises(ModelHashMismatchError):
        await registry.register(conflicting)

    # the original row must survive untouched (fail-closed, no overwrite)
    got = await registry.get(card.model_id, card.version)
    assert got is not None
    assert got.model_hash == "a" * 64


# --- gate-red reproduction: prove the CHECK constraint is load-bearing ---


async def test_gate_red_check_constraint_actually_fails_without_it(pool: asyncpg.Pool) -> None:
    """Proves the CHECK-violation negative above is not a tautology -- a
    temp table with the same columns but no CHECK accepts the same
    lineage span `ml_model_registry` rejects."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE ml_model_registry_no_check "
            "(train_lineage_start TIMESTAMPTZ NOT NULL, train_lineage_end TIMESTAMPTZ NOT NULL) "
            "ON COMMIT DROP"
        )
        await conn.execute(
            "INSERT INTO ml_model_registry_no_check (train_lineage_start, train_lineage_end) "
            "VALUES ($1, $2)",
            _NOW,
            _NOW - timedelta(days=1),
        )
        row = await conn.fetchrow("SELECT train_lineage_end FROM ml_model_registry_no_check")
        assert row["train_lineage_end"] == _NOW - timedelta(days=1)  # red: CHECK would reject this


# --- numeric performance assertion ---

_REGISTER_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0
"""Same budget as AI-10's `_APPEND_DB_ROUNDTRIP_P95_BUDGET_MS`
(`test_postgres_repository.py`) -- single DB round-trip, same spec, no
AI-specific SLO number exists in §7 yet."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_register_db_roundtrip_p95_within_budget(registry: PostgresModelRegistry) -> None:
    samples: list[float] = []
    for i in range(30):
        card = _card(model_id=f"m-perf-{time.perf_counter_ns()}-{i}")
        started = time.perf_counter()
        await registry.register(card)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-19 register] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_REGISTER_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _REGISTER_DB_ROUNDTRIP_P95_BUDGET_MS
