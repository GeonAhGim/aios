"""EO-04 적대적 — 동일 execution을 겨냥한 스케줄러 2개가 동시에 tick해도
리스를 쥔 한쪽만 실제로 주문을 시도함을 실DB(execution_leases) 위에서 증명.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §8
("스케줄러 인스턴스 2개를 같은 DB에 띄우고 동일 execution을 동시에 tick
시도 → 정확히 1회만 실행 경로가 실제로 주문을 시도했음을 mock adapter
호출 횟수로 검증"), §6("정상 종료 시 `release_all(owner_id)`로 즉시 다른
프로세스가 획득 가능"). owner_id가 다른 두 스케줄러가 같은 `pool`(같은
`execution_leases` 테이블)을 공유하는 것만으로 §4.1의 조건부 UPSERT가
실제 동시성 하에서도 상호배제를 지키는지 검증한다 — 단위테스트로는
증명할 수 없는 부분(진짜 두 커넥션이 동시에 같은 행을 놓고 경쟁).
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.data_distrust import DataDistrustMonitor
from src.data.models.trading import AccountBalance, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.services.background_loops import BackgroundLoops
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


async def _allow_all(_context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


def _resolver_for(adapters: dict[UUID, ExchangeAdapter]):
    async def resolve(user_id: UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError(f"{exchange} 자격증명 없음(테스트 리졸버)") from exc

    return resolve


def _filled_adapter() -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


def _scheduler(
    pool: asyncpg.Pool, *, resolve_adapter, owner_id: str
) -> ExecutionLoopScheduler:
    return ExecutionLoopScheduler(
        pool,
        resolve_adapter=resolve_adapter,
        policy=load_risk_policy(),
        pre_submit_gate=_allow_all,
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=PostgresExecutionLeaseRepository(pool),
        owner_id=owner_id,
    )


async def test_two_schedulers_ticking_same_execution_place_order_exactly_once(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    # 같은 어댑터 인스턴스를 두 스케줄러 모두에 물려, 둘 중 정확히 한쪽만
    # 실제로 place_order를 호출했는지 하나의 카운터로 셀 수 있게 한다.
    shared_adapter = _filled_adapter()
    resolve_adapter = _resolver_for({user_id: shared_adapter})
    scheduler_a = _scheduler(
        pool, resolve_adapter=resolve_adapter, owner_id=f"owner-a-{uuid.uuid4().hex[:8]}"
    )
    scheduler_b = _scheduler(
        pool, resolve_adapter=resolve_adapter, owner_id=f"owner-b-{uuid.uuid4().hex[:8]}"
    )

    report_a, report_b = await asyncio.gather(
        scheduler_a.tick_all_running(), scheduler_b.tick_all_running()
    )

    assert shared_adapter.place_order_call_count == 1
    ticked_by_a = execution_id in report_a.ticked
    ticked_by_b = execution_id in report_b.ticked
    assert ticked_by_a != ticked_by_b  # 정확히 한쪽만 리스를 획득해 tick했다(XOR).


async def test_background_loops_stop_releases_lease_for_immediate_reacquisition(pool):
    """§6 — `BackgroundLoops.stop()`은 TTL 만료를 기다리지 않고 즉시 리스를
    해제해, 다른 프로세스(owner_id)가 곧바로 인계받을 수 있어야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    owner_id = f"stop-owner-{uuid.uuid4().hex[:8]}"
    lease_repo = PostgresExecutionLeaseRepository(pool)
    # 만료까지 한참 남은 TTL로 리스를 쥔 상태를 만든다 — 아래 재획득이
    # 성공하면 "만료를 기다리지 않고" 즉시 넘겨졌다는 뜻이다.
    await lease_repo.acquire_or_renew_many(
        [execution_id], owner_id=owner_id, ttl_seconds=3600
    )
    scheduler = _scheduler(
        pool, resolve_adapter=_resolver_for({}), owner_id=owner_id
    )
    loops = BackgroundLoops(
        execution_scheduler=scheduler, lease_repo=lease_repo, owner_id=owner_id, tasks=[]
    )

    await loops.stop()

    other_owner = f"other-owner-{uuid.uuid4().hex[:8]}"
    reacquired = await lease_repo.acquire_or_renew_many(
        [execution_id], owner_id=other_owner, ttl_seconds=60
    )
    assert reacquired == {execution_id}
