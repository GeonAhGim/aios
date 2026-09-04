"""19.1 통합테스트 — 실제 dev DB 대상."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.portfolio_service import PortfolioService, RebalanceAdjustment, RebalanceError
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
def execution_service(pool):
    return ExecutionService(
        pool,
        load_risk_policy(),
        pre_start_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
    )


@pytest.fixture
def portfolio_service(pool):
    return PortfolioService(pool, load_risk_policy())


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


async def _link_credential(pool, user_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, 'bitget', $2, $2)",
            user_id,
            b"dummy",
        )


async def _create_running_execution(
    execution_service, pool, user_id, *, capital=Decimal("1000"), link_credential=True
):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    if link_credential:
        await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=capital,
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("100000"),
    )
    await execution_service.start(created.id, user_id)
    return created.id


async def test_no_executions_shows_all_cash(portfolio_service, pool):
    user_id = await create_test_user(pool)

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("5000"))

    assert view.allocations == []
    assert view.unallocated_cash == Decimal("5000")
    assert view.unallocated_cash_weight_pct == Decimal("100")


async def test_three_executions_weights_sum_to_100_percent(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    await _create_running_execution(execution_service, pool, user_id, capital=Decimal("1000"))
    await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("2000"), link_credential=False
    )
    await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("3000"), link_credential=False
    )

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("10000"))

    total_weight = view.unallocated_cash_weight_pct + sum(
        a.weight_pct for a in view.allocations
    )
    assert round(total_weight, 6) == Decimal("100")
    assert len(view.allocations) == 3


async def test_retired_execution_excluded_from_portfolio(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.retire(execution_id, user_id)

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("10000"))

    assert all(a.execution_id != execution_id for a in view.allocations)


async def test_pnl_included_in_current_value(execution_service, portfolio_service, pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("1000")
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'BTC/USDT', 'bitget',
                    (SELECT strategy_id FROM strategy_executions WHERE id = $2),
                    $2, 1.0, 50000, 150, 0, now())
            """,
            user_id,
            execution_id,
        )

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("10000"))

    allocation = next(a for a in view.allocations if a.execution_id == execution_id)
    assert allocation.current_value == Decimal("1150")


async def _create_running_execution_with_mode(
    execution_service, pool, user_id, *, mode, capital=Decimal("1000"), link_credential=True
):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    if link_credential:
        await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=capital,
        currency="USDT",
        exchange="bitget",
        mode=mode,
        available_balance=Decimal("100000"),
    )
    if mode == "LIVE":
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE approval_requests SET status = 'APPROVED' WHERE id = $1",
                created.approval_request_id,
            )
    await execution_service.start(created.id, user_id)
    return created.id


async def test_rebalance_decreases_allocation_without_touching_positions(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("2000")
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, entry_time)
            VALUES ($1, 'BTC/USDT', 'bitget',
                    (SELECT strategy_id FROM strategy_executions WHERE id = $2),
                    $2, 1.0, 50000, now())
            """,
            user_id,
            execution_id,
        )

    result = await portfolio_service.rebalance(
        user_id,
        [RebalanceAdjustment(execution_id=execution_id, new_allocated_capital=Decimal("500"))],
        total_cash_balance=Decimal("10000"),
    )

    assert result.adjusted == 1
    assert result.pending_approval == 0
    async with pool.acquire() as conn:
        allocated = await conn.fetchval(
            "SELECT allocated_capital FROM strategy_executions WHERE id = $1", execution_id
        )
        position_qty = await conn.fetchval(
            "SELECT quantity FROM positions WHERE execution_id = $1", execution_id
        )
    assert allocated == Decimal("500")
    assert position_qty == Decimal("1.0")  # 포지션은 전혀 건드려지지 않음


async def test_rebalance_increase_paper_needs_no_approval(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("500")
    )

    result = await portfolio_service.rebalance(
        user_id,
        [RebalanceAdjustment(execution_id=execution_id, new_allocated_capital=Decimal("800"))],
        total_cash_balance=Decimal("10000"),
    )

    assert result.pending_approval == 0
    assert result.approval_request_ids == []


async def test_rebalance_increase_live_triggers_approval(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution_with_mode(
        execution_service, pool, user_id, mode="LIVE", capital=Decimal("500")
    )

    result = await portfolio_service.rebalance(
        user_id,
        [RebalanceAdjustment(execution_id=execution_id, new_allocated_capital=Decimal("800"))],
        total_cash_balance=Decimal("10000"),
    )

    assert result.pending_approval == 1
    assert len(result.approval_request_ids) == 1


async def test_rebalance_rejects_total_exceeding_balance(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("500")
    )

    with pytest.raises(RebalanceError):
        await portfolio_service.rebalance(
            user_id,
            [
                RebalanceAdjustment(
                    execution_id=execution_id, new_allocated_capital=Decimal("2000")
                )
            ],
            total_cash_balance=Decimal("1000"),
        )

    async with pool.acquire() as conn:
        allocated = await conn.fetchval(
            "SELECT allocated_capital FROM strategy_executions WHERE id = $1", execution_id
        )
    assert allocated == Decimal("500")  # 거부 시 아무것도 반영되지 않음


async def test_rebalance_rejects_non_owner(execution_service, portfolio_service, pool):
    user_id = await create_test_user(pool)
    other_user = await create_test_user(pool)
    execution_id = await _create_running_execution(execution_service, pool, user_id)

    with pytest.raises(RebalanceError):
        await portfolio_service.rebalance(
            other_user,
            [
                RebalanceAdjustment(
                    execution_id=execution_id, new_allocated_capital=Decimal("100")
                )
            ],
            total_cash_balance=Decimal("10000"),
        )


async def test_rebalance_rejects_retired_execution(execution_service, portfolio_service, pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.retire(execution_id, user_id)

    with pytest.raises(RebalanceError):
        await portfolio_service.rebalance(
            user_id,
            [
                RebalanceAdjustment(
                    execution_id=execution_id, new_allocated_capital=Decimal("100")
                )
            ],
            total_cash_balance=Decimal("10000"),
        )


async def test_concurrent_rebalance_does_not_allow_combined_total_to_exceed_balance(
    execution_service, portfolio_service, pool, monkeypatch
):
    """docs/RED_TEAM_FINDINGS.md #09 회귀 — 트랜잭션/잠금 없이 동시에 두
    재구성 요청이 들어오면 서로의 아직 커밋 안 된 변경을 못 본 채 각자
    통과해버려 합산이 잔고를 초과할 수 있었다. FOR UPDATE로 잠근 뒤에는
    두 번째 요청이 첫 번째의 커밋을 기다렸다가 최신 값으로 재검증해야
    하므로 최소 하나는 거부돼야 한다.

    실제 asyncio.gather만으로는 두 코루틴의 SELECT가 겹친다는 보장이
    없어(이 세션에서 반복 확인한 사실), 첫 번째 호출의 SELECT 직후에
    실제 sleep을 살짝 끼워 넣어 두 번째 호출이 그 사이 반드시 진입하도록
    강제한다 — 잠금 자체는 이 인위적 지연이 아니라 실제 Postgres
    FOR UPDATE가 담당한다."""
    user_id = await create_test_user(pool)
    # 미인증 전략 배분 상한(10%, config/risk_policy.yaml)을 개별 조정 각각은
    # 넘지 않으면서(90/1000=9%) A+B 합산은 잔고를 초과하도록, 이미 잔고
    # 대부분을 쓰는 실행 C를 함께 둔다(850 + 90 + 90 = 1030 > 1000).
    await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("850")
    )
    exec_a = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("50"), link_credential=False
    )
    exec_b = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("50"), link_credential=False
    )

    call_count = {"n": 0}
    original_fetch = asyncpg.Connection.fetch

    async def patched_fetch(self, query, *args, **kwargs):
        result = await original_fetch(self, query, *args, **kwargs)
        if "SELECT id, allocated_capital FROM strategy_executions" in query:
            call_count["n"] += 1
            if call_count["n"] == 1:
                await asyncio.sleep(0.3)
        return result

    monkeypatch.setattr(asyncpg.Connection, "fetch", patched_fetch)

    async def rebalance_a():
        return await portfolio_service.rebalance(
            user_id,
            [RebalanceAdjustment(execution_id=exec_a, new_allocated_capital=Decimal("90"))],
            total_cash_balance=Decimal("1000"),
        )

    async def rebalance_b():
        return await portfolio_service.rebalance(
            user_id,
            [RebalanceAdjustment(execution_id=exec_b, new_allocated_capital=Decimal("90"))],
            total_cash_balance=Decimal("1000"),
        )

    task_a = asyncio.create_task(rebalance_a())
    await asyncio.sleep(0.05)  # task_a가 먼저 SELECT를 마치고 sleep에 들어가도록 보장
    task_b = asyncio.create_task(rebalance_b())

    results = await asyncio.gather(task_a, task_b, return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, RebalanceError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with pool.acquire() as conn:
        row_a = await conn.fetchval(
            "SELECT allocated_capital FROM strategy_executions WHERE id = $1", exec_a
        )
        row_b = await conn.fetchval(
            "SELECT allocated_capital FROM strategy_executions WHERE id = $1", exec_b
        )
    # 하나는 90(성공)으로 바뀌고 다른 하나는 50(원래값)에 그대로 남아야 한다
    # — 둘 다 90으로 바뀌면(180) #09가 재발한 것.
    assert row_a + row_b == Decimal("140")
