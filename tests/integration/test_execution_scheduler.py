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

import os
import uuid
from decimal import Decimal

import asyncpg
import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.data_distrust import DataDistrustMonitor
from src.data.models.trading import AccountBalance, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


def _asyncpg_dsn() -> str:
    # tests/conftest.py가 TEST_DATABASE_URL을 DATABASE_URL 환경변수로 옮겨
    # 두므로(이 worktree 전용 DB), 다른 통합테스트(execution_ownership/conftest.py
    # 등)와 동일하게 os.environ에서 읽는다 — .env 파일을 직접 파싱하면 이
    # override를 우회해 공유 dev DB(migrations 미적용 가능)로 잘못 붙는다.
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _allow_all(_context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


def _owner_id() -> str:
    # execution_leases는 이 테스트 세션 전체가 공유하는 테이블이라(트랜잭션
    # 롤백 격리 없음) 매 테스트마다 고유한 owner_id를 써야 다른 테스트가
    # 남긴 리스와 섞이지 않는다(test_postgres_lease_repository.py와 동일 이유).
    return f"scheduler-test-owner-{uuid.uuid4().hex[:8]}"


def _scheduler(pool: asyncpg.Pool, **overrides: object) -> ExecutionLoopScheduler:
    kwargs: dict[str, object] = dict(
        resolve_adapter=_resolver_for({}),
        policy=load_risk_policy(),
        pre_submit_gate=_allow_all,
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=PostgresExecutionLeaseRepository(pool),
        owner_id=_owner_id(),
    )
    kwargs.update(overrides)
    return ExecutionLoopScheduler(pool, **kwargs)  # type: ignore[arg-type]


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
    scheduler = _scheduler(
        pool, resolve_adapter=_resolver_for({user_a: adapter_a, user_b: adapter_b})
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

    scheduler = _scheduler(
        pool,
        resolve_adapter=_resolver_for(
            {user_ok: _filled_adapter(), user_broken: ExplodingAdapter()}
        ),
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
    scheduler = _scheduler(pool)

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
    scheduler = _scheduler(pool, resolve_adapter=_resolver_for({user: adapter}))

    candidate_ids = {row["id"] for row in await scheduler.list_candidates()}
    report = await scheduler.tick_all_running()

    assert live_id not in candidate_ids
    assert paused_id not in candidate_ids
    assert live_id not in report.ticked and paused_id not in report.ticked
    assert adapter.place_order_call_count == 0


async def test_execution_with_lease_held_by_other_owner_is_skipped(pool):
    """§4.1 — 다른 프로세스가 만료 전 리스를 쥔 execution_id는 RUNNING/PAPER라도
    이번 주기 tick 대상에서 빠진다(예외 없이 건너뜀). 같은 주기에 리스가 없는
    다른 execution은 정상적으로 tick된다."""
    user_leased, user_free = await create_test_user(pool), await create_test_user(pool)
    leased_id = await _create_execution(pool, user_leased, entry_threshold=100.0)
    free_id = await _create_execution(pool, user_free, entry_threshold=100.0)
    lease_repo = PostgresExecutionLeaseRepository(pool)
    await lease_repo.acquire_or_renew_many(
        [leased_id], owner_id="other-process-owner", ttl_seconds=60
    )
    adapter_leased, adapter_free = _filled_adapter(), _filled_adapter()
    scheduler = _scheduler(
        pool,
        resolve_adapter=_resolver_for({user_leased: adapter_leased, user_free: adapter_free}),
        lease_repo=lease_repo,
    )

    candidate_ids = {row["id"] for row in await scheduler.list_candidates()}
    report = await scheduler.tick_all_running()

    assert leased_id not in candidate_ids
    assert free_id in candidate_ids
    assert leased_id not in report.ticked
    assert leased_id not in report.failed
    assert free_id in report.ticked
    assert adapter_leased.place_order_call_count == 0
    assert adapter_free.place_order_call_count == 1


def test_interval_comes_from_risk_policy():
    policy = load_risk_policy()
    scheduler = _scheduler(None, policy=policy)  # type: ignore[arg-type]
    assert scheduler.interval_seconds == policy.execution_loop.interval_sec
