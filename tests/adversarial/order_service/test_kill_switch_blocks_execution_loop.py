"""EO-05 적대적 — safety_control이 ACTIVE일 때 실행 루프 스케줄러 경로로
tick해도 신규 주문 제출이 *시도조차* 되지 않음을 증명한다.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §8
("safety_control을 ACTIVE로 만든 뒤 스케줄러를 통해 tick → 신규 주문 제출이
시도조차 되지 않았음을 증명"), §2-B(`make_foundation_pre_submit_gate(pool)`을
실제 프로덕션 조립 경로 그대로 사용). `ExecutionLoopScheduler`는 EO-03/EO-04
이후 `pre_submit_gate`가 필수 인자라 이 테스트도 가짜 all-allow 게이트가
아닌 실제 kill switch 게이트를 주입해야 "우회 불가능성"을 증명한다 —
`tests/adversarial/execution_ownership/test_no_double_tick.py`가 리스
동시성을 증명하듯, 이 파일은 게이트 배선을 증명한다.

신호가 확실히 발화하도록 `tests/integration/test_execution_tick.py`의
`_create_execution` 헬퍼(entry_threshold=100.0, FakeExchangeAdapter가
closes=[50]*30을 돌려주면 BUY 신호가 매 tick 발화)를 그대로 재사용한다 —
kill switch가 없으면 이 설정으로 `place_order`가 반드시 1회 호출된다는
것은 `test_two_schedulers_ticking_same_execution_place_order_exactly_once`
(EO-04)가 이미 증명했다. 이 테스트는 그 대조군에 kill switch만 추가한다."""
from __future__ import annotations

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
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


def _resolver_for(adapter: ExchangeAdapter, user_id: UUID):
    async def resolve(candidate_user_id: UUID, exchange: str) -> ExchangeAdapter:
        if candidate_user_id != user_id:
            raise CredentialNotFoundError(f"{exchange} 자격증명 없음(테스트 리졸버)")
        return adapter

    return resolve


def _filled_adapter() -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


async def test_active_kill_switch_blocks_execution_loop_new_order_submission(
    pool: asyncpg.Pool,
) -> None:
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _filled_adapter()

    risk_repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="EO-05 적대적 테스트 — 실행 루프 kill switch",
    )

    scheduler = ExecutionLoopScheduler(
        pool,
        resolve_adapter=_resolver_for(adapter, user_id),
        policy=load_risk_policy(),
        pre_submit_gate=make_foundation_pre_submit_gate(pool),
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=PostgresExecutionLeaseRepository(pool),
        owner_id=f"kill-switch-test-{uuid.uuid4().hex[:8]}",
    )

    report = await scheduler.tick_all_running()

    assert adapter.place_order_call_count == 0
    assert execution_id not in report.failed
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert order_count == 0
    assert fsm_state == "IDLE"
