"""PostgresSourceContractRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. DoD(task-1764): 등급 승격이 `source_contract` 행의 UPDATE만으로
반영되고(같은 source_id, 새 PK 아님), 미등록 source_id 조회는 fail-closed
판정의 근거가 되도록 `None`을 정확히 반환함을 실 DB로 증명한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_source_contract import (
    PostgresSourceContractRepository,
)
from src.foundation.market_data.domain.entitlement.source_contract import (
    SourceContractDenialReason,
    SourceContractTier,
    authorize_source,
)

_NOW = datetime.now(timezone.utc)


async def _insert_contract(
    pool: asyncpg.Pool,
    *,
    source_id: str,
    tier: str,
    rate_limit: int,
    capability: dict[str, object],
    valid_to: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO source_contract
            (source_id, tier, credential_ref, redistribution_scope,
             rate_limit, quota, valid_from, valid_to, capability)
        VALUES ($1, $2, 'vault:test:v1', 'INTERNAL', $3, 1000, $4, $5, $6)
        """,
        source_id,
        tier,
        rate_limit,
        _NOW - timedelta(days=30),
        valid_to,
        json.dumps(capability),
    )


@pytest.fixture
def repo() -> PostgresSourceContractRepository:
    return PostgresSourceContractRepository()


async def test_get_unknown_source_id_returns_none(pool, repo) -> None:
    async with pool.acquire() as conn:
        result = await repo.get(conn, "NO_SUCH_SOURCE")
    assert result is None


async def test_tier_upgrade_is_a_row_update_not_a_new_row(pool, repo) -> None:
    source_id = f"TEST_SOURCE_{_NOW.timestamp()}"
    await _insert_contract(
        pool,
        source_id=source_id,
        tier=SourceContractTier.PERSONAL.value,
        rate_limit=10,
        capability={
            "asset_classes": ["EQUITY_KR"],
            "resolutions": ["1d"],
            "corporate_actions": False,
        },
    )

    async with pool.acquire() as conn:
        before = await repo.get(conn, source_id)
    assert before is not None
    assert before.tier == SourceContractTier.PERSONAL
    assert before.rate_limit == 10

    # 등급 승격: UPDATE 한 문장, 새 행 삽입이 아니다.
    await pool.execute(
        "UPDATE source_contract SET tier = $1, rate_limit = $2, capability = $3 "
        "WHERE source_id = $4",
        SourceContractTier.ENTERPRISE.value,
        10_000,
        json.dumps(
            {
                "asset_classes": ["EQUITY_KR", "EQUITY_US"],
                "resolutions": ["1d", "1m"],
                "corporate_actions": True,
            }
        ),
        source_id,
    )

    row_count = await pool.fetchval(
        "SELECT count(*) FROM source_contract WHERE source_id = $1", source_id
    )
    assert row_count == 1

    async with pool.acquire() as conn:
        after = await repo.get(conn, source_id)
    assert after is not None
    assert after.tier == SourceContractTier.ENTERPRISE
    assert after.rate_limit == 10_000
    assert after.capability.asset_classes == frozenset({"EQUITY_KR", "EQUITY_US"})


async def test_expired_row_is_denied_via_authorize_source(pool, repo) -> None:
    """negative: 실 DB에서 읽은 만료 계약이 순수 게이트에서 fail-closed로
    거부됨을 저장~판정 전 구간으로 증명한다."""
    source_id = f"TEST_EXPIRED_{_NOW.timestamp()}"
    await _insert_contract(
        pool,
        source_id=source_id,
        tier=SourceContractTier.BUSINESS.value,
        rate_limit=500,
        capability={
            "asset_classes": ["EQUITY_KR"],
            "resolutions": ["1d"],
            "corporate_actions": False,
        },
        valid_to=_NOW - timedelta(days=1),
    )

    async with pool.acquire() as conn:
        contract = await repo.get(conn, source_id)
    assert contract is not None

    grant = authorize_source(contract, _NOW)

    assert grant.allowed is False
    assert grant.denial_reason == SourceContractDenialReason.EXPIRED
