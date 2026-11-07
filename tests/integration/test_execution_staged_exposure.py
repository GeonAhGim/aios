"""J8 LP-4 통합: LIVE 전환은 초기 노출 단계로 시작하고, 확대는 별도 요청으로만 한다 (실제 DB)."""

from __future__ import annotations

import json
from decimal import Decimal

import asyncpg
import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.capital_allocation import CapitalAllocationError
from src.services.execution_service import ExecutionService
from src.services.execution_types import ExecutionControlError
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import create_test_tenant
from tests.integration.test_execution_convert_to_live import (
    _asyncpg_dsn,
    _create_approved_strategy,
    _link_credential,
)

BALANCE = Decimal("10000")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        # 전역 싱글톤 행 -- 다른 테스트가 남긴 halted 값과 섞이지 않게 매번 normal로 되돌린다
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


async def _row(pool, execution_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT allocated_capital, target_capital, max_loss_amount, max_drawdown_pct, status "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )


async def _audit(pool, action, execution_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_data FROM audit_log WHERE action_type = $1 AND target_id = $2 "
            "ORDER BY log_id",
            action,
            str(execution_id),
        )
    return [json.loads(r["decision_data"]) for r in rows]


async def test_conversion_starts_at_the_initial_stage_and_maps_the_loss_limit(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10", max_loss="25", target="500")

    row = await _row(pool, live.id)
    assert row["target_capital"] == Decimal("500")
    assert row["allocated_capital"] == Decimal("50")  # 500의 10%만 열린다
    assert row["max_loss_amount"] == Decimal("25")
    assert row["max_drawdown_pct"] == Decimal("50.00")  # 25 / 50 -> 기존 손실 가드가 강제
    # 응답도 거짓말하지 않는다 -- 목표가 아니라 지금 허용된 자본을 돌려준다
    assert live.allocated_capital == Decimal("50")
    assert live.max_drawdown_pct == Decimal("50.00")
    data = (await _audit(pool, "execution.converted_to_live", live.id))[0]
    assert Decimal(str(data["target_capital"])) == Decimal("500")
    assert Decimal(str(data["allocated_capital"])) == Decimal("50")


async def test_loss_limit_larger_than_the_stage_is_capped_at_full_capital(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10", max_loss="100", target="500")
    assert (await _row(pool, live.id))["max_drawdown_pct"] == Decimal("100.00")


async def test_database_forbids_a_stage_above_the_target(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE strategy_executions SET allocated_capital = 501 WHERE id = $1", live.id
            )


async def test_expand_raises_capital_recomputes_the_guard_and_audits(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10", max_loss="25")

    out = await service.expand_exposure(
        live.id, user_id, new_exposure_pct=Decimal("50"), available_balance=BALANCE
    )
    row = await _row(pool, live.id)
    assert row["allocated_capital"] == Decimal("250")
    assert row["max_drawdown_pct"] == Decimal("10.00")  # 25 / 250 -- 자본이 커졌으니 %는 내려간다
    assert out.allocated_capital == Decimal("250") and out.max_drawdown_pct == Decimal("10.00")
    data = (await _audit(pool, "execution.exposure_expanded", live.id))[0]
    assert Decimal(str(data["allocated_capital_before"])) == Decimal("50")
    assert Decimal(str(data["allocated_capital_after"])) == Decimal("250")
    assert Decimal(str(data["max_drawdown_pct_before"])) == Decimal("50.00")
    assert Decimal(str(data["max_drawdown_pct_after"])) == Decimal("10.00")


async def test_expand_to_the_full_target_is_allowed_but_not_beyond(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10")
    await service.expand_exposure(
        live.id, user_id, new_exposure_pct=Decimal("100"), available_balance=BALANCE
    )
    assert (await _row(pool, live.id))["allocated_capital"] == Decimal("500")
    with pytest.raises(ExecutionControlError, match="확대만"):
        await service.expand_exposure(
            live.id, user_id, new_exposure_pct=Decimal("100"), available_balance=BALANCE
        )


@pytest.mark.parametrize("pct", ["10", "5"])  # 같은 값, 줄이는 값
async def test_expand_refuses_equal_or_smaller_exposure(service, pool, pct):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10")
    with pytest.raises(ExecutionControlError, match="확대만"):
        await service.expand_exposure(
            live.id, user_id, new_exposure_pct=Decimal(pct), available_balance=BALANCE
        )
    assert (await _row(pool, live.id))["allocated_capital"] == Decimal("50")  # 바뀌지 않았다


async def test_expand_refuses_other_users_paper_unstaged_live_and_retired(service, pool):
    owner = await create_test_tenant(pool)
    paper = await _paper(service, pool, owner)
    live = await _convert(service, owner, paper.id)

    stranger = await create_test_tenant(pool)
    with pytest.raises(ExecutionControlError, match="본인"):
        await service.expand_exposure(
            live.id, stranger, new_exposure_pct=Decimal("50"), available_balance=BALANCE
        )
    with pytest.raises(ExecutionControlError, match="LIVE"):
        await service.expand_exposure(
            paper.id, owner, new_exposure_pct=Decimal("50"), available_balance=BALANCE
        )
    with pytest.raises(ExecutionControlError, match="존재하지"):
        await service.expand_exposure(
            999999999, owner, new_exposure_pct=Decimal("50"), available_balance=BALANCE
        )
    # 단계 없이 직접 만든 LIVE 실행(목표 자본 없음)은 확대 대상이 아니다
    strategy_id, version = await _create_approved_strategy(pool, owner)
    direct = await service.create_execution(
        owner,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="LIVE",
        available_balance=BALANCE,
    )
    with pytest.raises(ExecutionControlError, match="단계적 노출"):
        await service.expand_exposure(
            direct.id, owner, new_exposure_pct=Decimal("50"), available_balance=BALANCE
        )
    # 종료 전이는 대상이 아니다(승인 대기 실행은 retire가 거부한다) -- 종료 상태만 만든다
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'RETIRED', retired_at = now(), "
            "retire_liquidation = 'KEEP_POSITIONS' WHERE id = $1",
            live.id,
        )
    with pytest.raises(ExecutionControlError, match="종료"):
        await service.expand_exposure(
            live.id, owner, new_exposure_pct=Decimal("50"), available_balance=BALANCE
        )


async def test_expand_respects_the_balance_based_allocation_cap(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _paper(service, pool, user_id)
    live = await _convert(service, user_id, paper.id, pct="10")
    with pytest.raises(CapitalAllocationError):
        await service.expand_exposure(
            live.id, user_id, new_exposure_pct=Decimal("100"), available_balance=Decimal("500")
        )
    assert (await _row(pool, live.id))["allocated_capital"] == Decimal("50")
