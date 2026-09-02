"""FD-8 실행 루프 스케줄러 통합테스트 — RUNNING PAPER 실행 전부를 tick.

전수감사(docs/FULL_AUDIT_2026-09-02.md §3) 회귀: run_execution_tick은 완전했지만
운영 앱에서 호출되지 않았다. 이 테스트는 스케줄러가 실제 DB의 RUNNING 실행을
찾아 tick하고, 한 실행의 실패가 나머지를 막지 않으며, LIVE·비RUNNING 실행은
건드리지 않음을 실제 Postgres 위에서 검증한다.

공유 dev DB에는 다른 테스트가 남긴 RUNNING 실행이 있을 수 있다 — 어댑터
리졸버가 이 테스트의 사용자만 알고 나머지는 CredentialNotFoundError를 내므로
그 실행들은 "자격증명 없음"으로 건너뛰어진다. 따라서 단언은 "내 실행 ⊆ 결과"
형태로만 쓴다.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.data.models.trading import AccountBalance, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _filled_adapter() -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


def _resolver_for(adapters: dict[uuid.UUID, ExchangeAdapter]):
    async def resolve(user_id: uuid.UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError(f"{exchange} 자격증명 없음(테스트 리졸버)") from exc

    return resolve


async def _fsm_state(pool: asyncpg.Pool, execution_id: int) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )


async def test_tick_all_running_ticks_every_paper_execution(pool):
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    exec_a = await _create_execution(pool, user_a, entry_threshold=100.0)
    exec_b = await _create_execution(pool, user_b, entry_threshold=100.0)
    adapter_a, adapter_b = _filled_adapter(), _filled_adapter()
    scheduler = ExecutionLoopScheduler(
        pool,
        resolve_adapter=_resolver_for({user_a: adapter_a, user_b: adapter_b}),
        policy=load_risk_policy(),
    )

    report = await scheduler.tick_all_running()

    assert {exec_a, exec_b} <= set(report.ticked)
    assert not ({exec_a, exec_b} & set(report.failed))
    assert adapter_a.place_order_call_count == 1
    assert adapter_b.place_order_call_count == 1
    assert await _fsm_state(pool, exec_a) == "HOLDING"
    assert await _fsm_state(pool, exec_b) == "HOLDING"


async def test_one_failing_execution_does_not_block_the_others(pool):
    user_ok = await create_test_user(pool)
    user_broken = await create_test_user(pool)
    exec_ok = await _create_execution(pool, user_ok, entry_threshold=100.0)
    exec_broken = await _create_execution(pool, user_broken, entry_threshold=100.0)

    class ExplodingAdapter(FakeExchangeAdapter):
        async def get_ohlcv(self, symbol, timeframe, limit=100):
            raise RuntimeError("거래소 장애(테스트)")

    scheduler = ExecutionLoopScheduler(
        pool,
        resolve_adapter=_resolver_for(
            {user_ok: _filled_adapter(), user_broken: ExplodingAdapter()}
        ),
        policy=load_risk_policy(),
    )

    report = await scheduler.tick_all_running()

    assert exec_ok in report.ticked
    assert exec_broken in report.failed
    assert "RuntimeError" in report.failed[exec_broken]
    assert await _fsm_state(pool, exec_ok) == "HOLDING"
    assert await _fsm_state(pool, exec_broken) == "IDLE"


async def test_missing_credential_is_skipped_not_failed(pool):
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user, entry_threshold=100.0)
    scheduler = ExecutionLoopScheduler(
        pool, resolve_adapter=_resolver_for({}), policy=load_risk_policy()
    )

    report = await scheduler.tick_all_running()

    assert execution_id in report.skipped_no_credential
    assert execution_id not in report.ticked
    assert execution_id not in report.failed


async def test_live_and_paused_executions_are_never_ticked(pool):
    user = await create_test_user(pool)
    live_id = await _create_execution(pool, user, entry_threshold=100.0)
    paused_id = await _create_execution(pool, user, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET mode = 'LIVE' WHERE id = $1", live_id
        )
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'USER' WHERE id = $1",
            paused_id,
        )
    adapter = _filled_adapter()
    scheduler = ExecutionLoopScheduler(
        pool, resolve_adapter=_resolver_for({user: adapter}), policy=load_risk_policy()
    )

    runnable_ids = {row["id"] for row in await scheduler.list_runnable()}
    report = await scheduler.tick_all_running()

    assert live_id not in runnable_ids
    assert paused_id not in runnable_ids
    assert live_id not in report.ticked and paused_id not in report.ticked
    assert adapter.place_order_call_count == 0


def test_interval_comes_from_risk_policy():
    policy = load_risk_policy()
    scheduler = ExecutionLoopScheduler(None, resolve_adapter=_resolver_for({}), policy=policy)  # type: ignore[arg-type]
    assert scheduler.interval_seconds == policy.execution_loop.interval_sec
