"""16.3/16.5 통합테스트 — 실행 상태 제어(시작/일시정지/중지) 실제 dev DB 대상.

16.5 핵심 완료조건(Watchdog PAUSED 상태에서 사용자 시작 시도 거부)은
test_safety_layer_pause_blocks_user_restart가 실증한다 — 별도 리프
파일을 만들 만큼 다른 관심사가 아니라 ExecutionService.start()의
핵심 요구사항이라 이 파일에서 함께 다룬다.
"""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_service import ExecutionControlError, ExecutionService
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return ExecutionService(pool, load_risk_policy())


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _link_credential(pool, user_id, exchange="bitget"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, $2, $3, $3)",
            user_id,
            exchange,
            b"dummy",
        )


async def _create_execution(service, pool, user_id, *, mode="PAPER"):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    result = await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode=mode,
        available_balance=Decimal("10000"),
    )
    return result


async def test_start_paper_execution_transitions_to_running(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")

    result = await service.start(created.id, user_id)

    assert result.status == "RUNNING"


async def test_start_live_execution_blocked_until_approved(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id, mode="LIVE")

    with pytest.raises(ExecutionControlError):
        await service.start(created.id, user_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET status = 'APPROVED' WHERE id = $1",
            created.approval_request_id,
        )

    result = await service.start(created.id, user_id)
    assert result.status == "RUNNING"


async def test_cannot_start_retired_execution(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)
    await service.start(created.id, user_id)
    await service.retire(created.id, user_id)

    with pytest.raises(ExecutionControlError):
        await service.start(created.id, user_id)


async def test_start_rejects_non_owner(service, pool):
    user_id = await create_test_user(pool)
    other_user = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)

    with pytest.raises(ExecutionControlError):
        await service.start(created.id, other_user)


async def test_pause_running_execution_by_user(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)
    await service.start(created.id, user_id)

    result = await service.pause(created.id, paused_by="USER", user_id=user_id)

    assert result.status == "PAUSED"


async def test_user_can_restart_own_paused_execution(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)
    await service.start(created.id, user_id)
    await service.pause(created.id, paused_by="USER", user_id=user_id)

    result = await service.start(created.id, user_id)

    assert result.status == "RUNNING"


async def test_safety_layer_pause_blocks_user_restart(service, pool):
    """16.5 핵심 완료조건 — 안전장치 우선순위(8.6-B)가 사용자 버튼보다
    우선한다."""
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)
    await service.start(created.id, user_id)

    await service.pause(created.id, paused_by="SAFETY_LAYER")

    with pytest.raises(ExecutionControlError):
        await service.start(created.id, user_id)


async def test_pause_rejects_non_running_execution(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)  # PENDING_APPROVAL

    with pytest.raises(ExecutionControlError):
        await service.pause(created.id, paused_by="USER", user_id=user_id)


async def test_retire_running_execution(service, pool):
    user_id = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)
    await service.start(created.id, user_id)

    result = await service.retire(created.id, user_id, liquidation="KEEP_POSITIONS")

    assert result.status == "RETIRED"


async def test_retire_rejects_non_owner(service, pool):
    user_id = await create_test_user(pool)
    other_user = await create_test_user(pool)
    created = await _create_execution(service, pool, user_id)
    await service.start(created.id, user_id)

    with pytest.raises(ExecutionControlError):
        await service.retire(created.id, other_user)
