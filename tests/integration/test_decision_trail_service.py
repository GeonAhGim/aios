"""J8 trail W2 통합: DecisionTrailService -- audit_log·risk_decision 합성 (실제 DB).

실행 전환·확대·위험 결정 이력을 DecisionTrailService.get_trail(execution_id, user_id)로
읽어 W1 build_decision_trail에 넘긴 결과의 정확성을 검증한다.

테스트 시나리오:
1. 전환 후 확대한 실행이 CONVERSION->EXPANSION 순으로 나온다.
2. 다른 사용자 조회는 ExecutionControlError로 거부한다.
3. 없는 실행 id는 ExecutionControlError로 거부한다.
4. 기록 없는 실행은 빈 목록을 반환한다.
5. risk_decision 행을 직접 INSERT해 RISK_DECISION이 시간순으로 섞인다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.decision_trail import TrailKind
from src.services.decision_trail_service import DecisionTrailService
from src.services.execution_service import ExecutionService
from src.services.execution_types import ExecutionControlError
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import _asyncpg_dsn, create_test_tenant
from tests.integration.test_execution_convert_to_live import (
    _create_approved_strategy,
    _link_credential,
)

BALANCE = Decimal("10000")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return ExecutionService(
        pool,
        load_risk_policy(),
        pre_start_gate=make_foundation_pre_submit_gate(
            pool, require_mandate=False, require_compliance_mandate=False
        ),
    )


@pytest.fixture
def trail_service(pool):
    return DecisionTrailService(pool)


async def _paper(service, pool, user_id):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    return await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=BALANCE,
    )


async def _convert(service, user_id, paper_id, *, pct="10", max_loss="100", target="500"):
    return await service.convert_to_live(
        user_id,
        paper_id,
        allocated_capital=Decimal(target),
        currency="USDT",
        exchange="bitget",
        available_balance=BALANCE,
        max_loss_amount=Decimal(max_loss),
        initial_exposure_pct=Decimal(pct),
    )


async def _expand(service, user_id, live_id):
    return await service.expand_exposure(
        live_id, user_id, new_exposure_pct=Decimal("50"), available_balance=BALANCE
    )


async def _insert_risk_decision(
    pool,
    execution_ref: str,
    tenant_id,
    *,
    evaluated_at,
    outcome="ALLOW",
    gate_kind="PRE_SUBMIT",
    rule_version="v1",
):
    """Insert a risk_decision row directly for testing."""
    decision_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO risk_decision
                (decision_id, tenant_id, gate_kind, execution_ref,
                 subject_fingerprint, outcome, reason_codes, obligations,
                 rule_results, rule_version, rule_hash, engine_version,
                 inputs_hash, inputs_snapshot, input_refs,
                 trace_id, evaluated_at, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18)
            """,
            decision_id,
            tenant_id,
            gate_kind,
            execution_ref,
            "0" * 64,
            outcome,
            ["test_code"],
            ["obligation_1"],
            json.dumps([]),
            rule_version,
            "0" * 64,
            "v1",
            "0" * 64,
            json.dumps({}),
            [],
            uuid4(),
            evaluated_at,
            evaluated_at + timedelta(days=1),
        )
    return decision_id


async def test_trail_shows_conversion_then_expansion_order(service, pool, trail_service):
    """전환 후 확대한 실행이 CONVERSION->EXPANSION 순으로 나온다."""
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10")
    await _expand(service, user_id, live.id)

    entries = await trail_service.get_trail(live.id, user_id)
    kinds = [e.kind for e in entries]
    assert kinds == [TrailKind.CONVERSION, TrailKind.EXPANSION]
    assert await trail_service.get_trail(paper.id, user_id) == []
    conversion_idx = kinds.index(TrailKind.CONVERSION)
    expansion_idx = kinds.index(TrailKind.EXPANSION)
    assert conversion_idx < expansion_idx, (
        f"CONVERSION({conversion_idx})이(가) EXPANSION({expansion_idx})보다 먼저야 합니다"
    )


async def test_trail_rejects_different_user(service, pool, trail_service):
    """다른 사용자 조회는 ExecutionControlError로 거부한다."""
    owner_id = await create_test_tenant(pool)
    other_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, owner_id)
    live = await _convert(service, owner_id, paper.id, pct="10")

    with pytest.raises(ExecutionControlError, match="본인"):
        await trail_service.get_trail(live.id, other_id)


async def test_trail_rejects_nonexistent_execution(pool, trail_service):
    """없는 실행 id는 ExecutionControlError로 거부한다."""
    fake_user = uuid4()
    with pytest.raises(ExecutionControlError, match="존재하지"):
        await trail_service.get_trail(-1, fake_user)


async def test_trail_empty_for_execution_with_no_records(service, pool, trail_service):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    assert await trail_service.get_trail(paper.id, user_id) == []


async def test_trail_merges_risk_decision(service, pool, trail_service):
    """risk_decision 행을 직접 INSERT해 RISK_DECISION이 시간순으로 섞인다."""
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10")

    # 위험 결정 두 행을 INSERT (하나는 전환 이전, 하나는 이후)
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")

    await _insert_risk_decision(
        pool,
        str(live.id),
        user_id,
        evaluated_at=now - timedelta(seconds=10),
        outcome="DENY",
        gate_kind="PRE_TRADE",
    )
    await _insert_risk_decision(
        pool,
        str(live.id),
        user_id,
        evaluated_at=now + timedelta(seconds=5),
        outcome="ALLOW",
        gate_kind="INTRADAY",
    )

    entries = await trail_service.get_trail(live.id, user_id)
    risk_entries = [e for e in entries if e.kind == TrailKind.RISK_DECISION]
    assert len(risk_entries) == 2
    assert [e.kind for e in entries] == [
        TrailKind.RISK_DECISION, TrailKind.CONVERSION, TrailKind.RISK_DECISION
    ]
    # 시간순 정렬 확인
    for i in range(len(risk_entries) - 1):
        assert risk_entries[i].occurred_at <= risk_entries[i + 1].occurred_at


async def test_trail_excludes_other_tenant_and_execution(service, pool, trail_service):
    owner = await create_test_tenant(pool)
    other = await create_test_tenant(pool)
    paper = await _paper(service, pool, owner)
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
    await _insert_risk_decision(pool, str(paper.id), other, evaluated_at=now)
    await _insert_risk_decision(pool, "-1", owner, evaluated_at=now)
    assert await trail_service.get_trail(paper.id, owner) == []


async def test_trail_propagates_database_failure(service, pool, trail_service, monkeypatch):
    from src.services import decision_trail_service as module

    owner = await create_test_tenant(pool)
    paper = await _paper(service, pool, owner)
    monkeypatch.setattr(
        module, "_RISK_DECISION_SQL",
        "SELECT 1 / (length($1::text) - length($1::text)), $2::uuid",
    )
    with pytest.raises(asyncpg.DivisionByZeroError):
        await trail_service.get_trail(paper.id, owner)


async def test_trail_limits_risk_history_to_latest_200(service, pool, trail_service):
    owner = await create_test_tenant(pool)
    paper = await _paper(service, pool, owner)
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT now()")
    for offset in range(201):
        await _insert_risk_decision(
            pool, str(paper.id), owner, evaluated_at=now + timedelta(seconds=offset)
        )
    entries = await trail_service.get_trail(paper.id, owner)
    assert len(entries) == 200
    assert entries[0].occurred_at == now + timedelta(seconds=1)
    assert entries[-1].occurred_at == now + timedelta(seconds=200)
    assert all(entry.kind == TrailKind.RISK_DECISION for entry in entries)
