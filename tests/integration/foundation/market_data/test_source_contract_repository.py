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
from pydantic import ValidationError

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


async def test_not_yet_valid_row_is_denied_via_authorize_source(pool, repo) -> None:
    """negative: valid_from이 아직 오지 않은 계약은 실 DB 왕복 후에도
    fail-closed로 거부된다(EXPIRED와는 다른 사유)."""
    source_id = f"TEST_NOT_YET_VALID_{_NOW.timestamp()}"
    await pool.execute(
        """
        INSERT INTO source_contract
            (source_id, tier, credential_ref, redistribution_scope,
             rate_limit, quota, valid_from, valid_to, capability)
        VALUES ($1, $2, 'vault:test:v1', 'INTERNAL', $3, 1000, $4, $5, $6)
        """,
        source_id,
        SourceContractTier.PERSONAL.value,
        10,
        _NOW + timedelta(days=1),
        None,
        json.dumps(
            {"asset_classes": ["EQUITY_KR"], "resolutions": ["1d"], "corporate_actions": False}
        ),
    )

    async with pool.acquire() as conn:
        contract = await repo.get(conn, source_id)
    assert contract is not None

    grant = authorize_source(contract, _NOW)

    assert grant.allowed is False
    assert grant.denial_reason == SourceContractDenialReason.NOT_YET_VALID


async def test_insert_invalid_tier_rejected_by_check_constraint(pool) -> None:
    """negative: DB의 tier CHECK 제약을 우회한 값은 삽입 자체가 거부된다
    (도메인 enum 밖의 값이 조용히 저장되지 않음, fail-closed)."""
    source_id = f"TEST_INVALID_TIER_{_NOW.timestamp()}"
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_contract(
            pool,
            source_id=source_id,
            tier="NOT_A_REAL_TIER",
            rate_limit=10,
            capability={
                "asset_classes": ["EQUITY_KR"],
                "resolutions": ["1d"],
                "corporate_actions": False,
            },
        )


async def test_insert_valid_to_before_valid_from_rejected_by_check_constraint(pool) -> None:
    """negative: valid_to <= valid_from인 행은 DB CHECK 제약이 거부한다
    (불변식 위반 입력이 저장되지 않음)."""
    source_id = f"TEST_BAD_RANGE_{_NOW.timestamp()}"
    with pytest.raises(asyncpg.CheckViolationError):
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
            valid_to=_NOW - timedelta(days=31),
        )


async def test_get_malformed_capability_raises_instead_of_silently_parsing(pool, repo) -> None:
    """negative: capability JSONB에 필수 키(asset_classes)가 빠지면 repo.get()이
    조용히 기본값을 채우지 않고 예외로 실패한다(fail-closed)."""
    source_id = f"TEST_MALFORMED_CAP_{_NOW.timestamp()}"
    await _insert_contract(
        pool,
        source_id=source_id,
        tier=SourceContractTier.PERSONAL.value,
        rate_limit=10,
        capability={"resolutions": ["1d"], "corporate_actions": False},
    )

    async with pool.acquire() as conn:
        with pytest.raises((KeyError, ValidationError)):
            await repo.get(conn, source_id)


async def test_get_propagates_connection_failure_instead_of_swallowing_it(repo) -> None:
    """실패주입: 하위 conn.fetchrow가 예외를 던지면 repo.get()은 이를
    삼키거나 None으로 위장하지 않고 그대로 전파한다(fail-closed)."""

    class _FailingConn:
        async def fetchrow(self, *_args: object, **_kwargs: object) -> None:
            raise asyncpg.PostgresConnectionError("simulated connection drop")

    with pytest.raises(asyncpg.PostgresConnectionError):
        await repo.get(_FailingConn(), "ANY_SOURCE")
