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
import time
import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.data_distrust import DataDistrustMonitor
from src.data.models.trading import AccountBalance, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.services.background_loops import BackgroundLoops
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler, TickReport
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


async def _allow_all(_context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


def _resolver_for(adapters: dict[UUID, ExchangeAdapter]):
    async def resolve(user_id: UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError(f"{exchange} 자격증명 없음(테스트 리졸버)") from exc

    return resolve


def _filled_adapter() -> FakeExchangeAdapter:
    # min_bars(config/risk_policy.yaml var.min_bars=60) 미달이면 R-11
    # var_es 룰이 결손(DENY) 취급한다 — RiskEngine 통과에 필요한 최소치보다
    # 넉넉히 준다(tests/integration/test_execution_tick.py와 동일한 65).
    return FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


def _scheduler(pool: asyncpg.Pool, *, resolve_adapter, owner_id: str) -> ExecutionLoopScheduler:
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
    user_id = await create_test_tenant(pool)
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
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    owner_id = f"stop-owner-{uuid.uuid4().hex[:8]}"
    lease_repo = PostgresExecutionLeaseRepository(pool)
    # 만료까지 한참 남은 TTL로 리스를 쥔 상태를 만든다 — 아래 재획득이
    # 성공하면 "만료를 기다리지 않고" 즉시 넘겨졌다는 뜻이다.
    await lease_repo.acquire_or_renew_many([execution_id], owner_id=owner_id, ttl_seconds=3600)
    scheduler = _scheduler(pool, resolve_adapter=_resolver_for({}), owner_id=owner_id)
    loops = BackgroundLoops(
        execution_scheduler=scheduler, lease_repo=lease_repo, owner_id=owner_id, tasks=[]
    )

    await loops.stop()

    other_owner = f"other-owner-{uuid.uuid4().hex[:8]}"
    reacquired = await lease_repo.acquire_or_renew_many(
        [execution_id], owner_id=other_owner, ttl_seconds=60
    )
    assert reacquired == {execution_id}


async def test_other_owner_holding_valid_lease_excludes_candidate_and_order(pool):
    """negative — owner_a가 아직 만료 전인 유효한 리스를 쥐고 있으면, owner_b는
    `list_candidates`에서 그 execution을 아예 보지 못해 `place_order`를 절대
    호출하지 않는다(§4.1 상호배제를 단일 경쟁자만으로도 명시적으로 거부)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    shared_adapter = _filled_adapter()
    resolve_adapter = _resolver_for({user_id: shared_adapter})
    owner_a = f"owner-a-{uuid.uuid4().hex[:8]}"
    owner_b = f"owner-b-{uuid.uuid4().hex[:8]}"

    lease_repo = PostgresExecutionLeaseRepository(pool)
    await lease_repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=3600)

    scheduler_b = _scheduler(pool, resolve_adapter=resolve_adapter, owner_id=owner_b)
    report_b = await scheduler_b.tick_all_running()

    assert execution_id not in report_b.ticked
    assert shared_adapter.place_order_call_count == 0


async def test_stale_owner_excluded_after_lease_stolen_by_expiry(pool):
    """negative — owner_a의 리스가 만료돼 owner_b가 인계받은 뒤, 그 사실을
    모르는 owner_a(재시작 지연·네트워크 파티션 등으로 자신이 이미 리스를
    뺏긴 줄 모르는 상황)가 다시 tick을 시도해도 이제는 후보 목록에서
    제외돼 `place_order`를 추가로 호출하지 않는다 — 소유권을 잃은 프로세스가
    뒤늦게 또 주문을 넣는 진짜 이중 실행 사고를 명시적으로 거부한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    shared_adapter = _filled_adapter()
    resolve_adapter = _resolver_for({user_id: shared_adapter})
    owner_a = f"owner-a-{uuid.uuid4().hex[:8]}"
    owner_b = f"owner-b-{uuid.uuid4().hex[:8]}"

    lease_repo = PostgresExecutionLeaseRepository(pool)
    # owner_a의 리스를 즉시 만료 상태로 심어둔다.
    await lease_repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=-1)

    scheduler_b = _scheduler(pool, resolve_adapter=resolve_adapter, owner_id=owner_b)
    report_b = await scheduler_b.tick_all_running()
    assert execution_id in report_b.ticked
    assert shared_adapter.place_order_call_count == 1

    scheduler_a = _scheduler(pool, resolve_adapter=resolve_adapter, owner_id=owner_a)
    report_a = await scheduler_a.tick_all_running()

    assert execution_id not in report_a.ticked
    assert shared_adapter.place_order_call_count == 1  # owner_a는 추가로 주문하지 않았다.


async def test_three_schedulers_racing_same_execution_place_order_exactly_once(pool):
    """negative — 경쟁자를 2개에서 3개로 늘려도 §4.1 상호배제가 깨지지 않고
    정확히 1개만 리스를 획득해 tick함을 확인한다(경쟁자 수 증가가 이중 실행
    확률을 높이지 않는다는 불변식을 더 강한 동시성으로 재확인)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    shared_adapter = _filled_adapter()
    resolve_adapter = _resolver_for({user_id: shared_adapter})
    schedulers = [
        _scheduler(
            pool, resolve_adapter=resolve_adapter, owner_id=f"owner-{i}-{uuid.uuid4().hex[:8]}"
        )
        for i in range(3)
    ]

    reports = await asyncio.gather(*(s.tick_all_running() for s in schedulers))

    assert shared_adapter.place_order_call_count == 1
    ticked_count = sum(1 for r in reports if execution_id in r.ticked)
    assert ticked_count == 1


async def test_lease_repo_failure_is_fail_closed_and_does_not_block_other_owner(pool):
    """실패주입 — owner_a의 리스 저장소가 `acquire_or_renew_many`에서 DB 커넥션
    장애(`asyncpg.PostgresConnectionError`)를 던지면 owner_a의 이번 주기는
    예외로 실패해야 하고(결과를 빈 집합으로 위장해 "이번 주기엔 후보 없음"
    으로 삼키면 안 된다), 그 실패가 owner_b의 정상적인 리스 획득·tick을
    막지 않아야 한다 — DB 반쪽 장애가 이중 실행이나 전체 락업으로 번지지
    않음을 증명한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    shared_adapter = _filled_adapter()
    resolve_adapter = _resolver_for({user_id: shared_adapter})
    owner_a = f"owner-a-{uuid.uuid4().hex[:8]}"
    owner_b = f"owner-b-{uuid.uuid4().hex[:8]}"

    class _FailingLeaseRepo(PostgresExecutionLeaseRepository):
        async def acquire_or_renew_many(self, execution_ids, *, owner_id, ttl_seconds):
            raise asyncpg.exceptions.PostgresConnectionError("simulated DB outage")

    scheduler_a = ExecutionLoopScheduler(
        pool,
        resolve_adapter=resolve_adapter,
        policy=load_risk_policy(),
        pre_submit_gate=_allow_all,
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=_FailingLeaseRepo(pool),
        owner_id=owner_a,
    )
    scheduler_b = _scheduler(pool, resolve_adapter=resolve_adapter, owner_id=owner_b)

    results = await asyncio.gather(
        scheduler_a.tick_all_running(), scheduler_b.tick_all_running(), return_exceptions=True
    )

    assert isinstance(results[0], asyncpg.exceptions.PostgresConnectionError)
    assert isinstance(results[1], TickReport)
    assert execution_id in results[1].ticked
    assert shared_adapter.place_order_call_count == 1


@pytest.mark.perf
async def test_five_schedulers_race_completes_within_budget(pool):
    """성능 단언 — 경쟁자를 5개로 늘려도 tick_all_running()이 로컬 budget
    5초 안에 끝난다(§7 "1회 왕복" 배치 UPSERT 설계가 경쟁자 수에 선형으로
    지연을 만들지 않음을 수치로 고정 — split_brain DEEPEN(task-4114)과
    동일한 자체 정의 budget 관례)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    shared_adapter = _filled_adapter()
    resolve_adapter = _resolver_for({user_id: shared_adapter})
    schedulers = [
        _scheduler(
            pool, resolve_adapter=resolve_adapter, owner_id=f"perf-{i}-{uuid.uuid4().hex[:8]}"
        )
        for i in range(5)
    ]

    started = time.monotonic()
    reports = await asyncio.gather(*(s.tick_all_running() for s in schedulers))
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert shared_adapter.place_order_call_count == 1
    ticked_count = sum(1 for r in reports if execution_id in r.ticked)
    assert ticked_count == 1
