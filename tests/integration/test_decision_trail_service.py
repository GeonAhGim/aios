# ruff: noqa: F401, F811
"""J8 trail W2 integration: DecisionTrailService against the real DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.services.decision_trail import TrailKind
from src.services.decision_trail_service import DecisionTrailService
from src.services.execution_types import ExecutionControlError
from tests.integration.conftest import create_test_tenant
from tests.integration.test_execution_staged_exposure import (
    _convert,
    _paper,
    pool,
    service,
)


async def _insert_risk(pool, tenant_id, execution_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO risk_decision (decision_id, tenant_id, gate_kind, execution_ref, "
            "subject_fingerprint, outcome, reason_codes, rule_version, rule_hash, "
            "engine_version, inputs_hash, inputs_snapshot, trace_id, evaluated_at, expires_at) "
            "VALUES ($1, $2, 'PRE_TRADE', $3, $4, 'DENY', ARRAY['X'], 'v1', $4, 'e1', $4, "
            "'{}'::jsonb, $5, $6, $6)",
            uuid.uuid4(),
            tenant_id,
            str(execution_id),
            "a" * 64,
            uuid.uuid4(),
            datetime.now(UTC),
        )


async def test_conversion_then_expansion_in_order(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id)
    await service.expand_exposure(
        live.id, user_id, new_exposure_pct=Decimal("50"), available_balance=Decimal("10000")
    )
    trail = await DecisionTrailService(pool).get_trail(live.id, user_id)
    assert [e.kind for e in trail] == [TrailKind.CONVERSION, TrailKind.EXPANSION]


async def test_other_user_is_rejected(service, pool):
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    paper = await _paper(service, pool, owner)
    with pytest.raises(ExecutionControlError, match="본인"):
        await DecisionTrailService(pool).get_trail(paper.id, stranger)


async def test_missing_execution_is_rejected(pool):
    user_id = await create_test_tenant(pool)
    with pytest.raises(ExecutionControlError, match="존재하지"):
        await DecisionTrailService(pool).get_trail(2**62, user_id)


async def test_execution_without_records_gives_empty_trail(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    assert await DecisionTrailService(pool).get_trail(paper.id, user_id) == []


async def test_risk_decision_rows_are_merged(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id)
    await _insert_risk(pool, user_id, live.id)
    trail = await DecisionTrailService(pool).get_trail(live.id, user_id)
    assert {e.kind for e in trail} == {TrailKind.CONVERSION, TrailKind.RISK_DECISION}


async def test_other_tenants_risk_rows_are_not_leaked(service, pool):
    user_id = await create_test_tenant(pool)
    other = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    await _insert_risk(pool, other, paper.id)
    assert await DecisionTrailService(pool).get_trail(paper.id, user_id) == []
