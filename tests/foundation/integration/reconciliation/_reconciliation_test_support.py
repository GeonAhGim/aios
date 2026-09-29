"""Shared fixtures/helpers for `test_reconciliation_*.py` (split at 500-LOC
policy, ADR-2026-09-10-C §7) -- both files import from here instead of
duplicating the DB/entity-snapshot scaffolding."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.contracts.v1 import EntitySnapshot
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresReconciliationRepository(pool)


@pytest.fixture
def connection_repo(pool):
    return PostgresConnectionRepository(pool)


@pytest.fixture
def risk_repo(pool):
    return PostgresRiskGateRepository(pool)


async def tenant(pool):
    return await create_test_tenant(pool)


def matching_entities() -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT",
            internal_value=Decimal("1000.00"),
            provider_value=Decimal("1000.00"),
        )
    ]


def mismatched_entities() -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT",
            internal_value=Decimal("1000.00"),
            provider_value=Decimal("400.00"),
        )
    ]


def minor_difference_entities() -> list[EntitySnapshot]:
    """diff=0.50, relative=0.05% — both within policy tolerance
    (absolute_tolerance=0.01 OR relative_tolerance_pct=0.1%) so
    classify_item() returns MINOR_DIFFERENCE, not HEALTHY (diff != 0)."""
    return [
        EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT",
            internal_value=Decimal("1000.00"),
            provider_value=Decimal("999.50"),
        )
    ]
