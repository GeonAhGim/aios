"""E2E-1 페이퍼 주문 생명주기 — 신호->리스크->컴플라이언스->주문->체결/
취소/거부/(만료는 N/A) -> position/ledger 반영, 풀스택.

ADR-2026-09-24-A Decision 3 — depth가 아직 미평가(D?)인 리프들의 대체
green-check로 쓰이는 E2E 시나리오. 이 파일의 각 테스트가 실제로 거치는
코드 경로와, 그 경로가 커버하는 스펙 리프 ID는 다음과 같다:

- FD-8.1~8.4(`src/services/execution_loop/tick.py`, `run_execution_tick`)
  — StrategyEngine->PortfolioEngine->RiskEngine->Executor 전체 오케스트
  레이션, IDLE/HOLDING -> *_PENDING FSM 전이 시점(Executor 호출 직전).
- R-32/R-36 — 사전거래 리스크 단계(WORM `risk_decision` 기록)와 원자적
  fence+control 읽기.
- CM-8/CM-A5 — `make_foundation_pre_submit_gate`의 2층 컴플라이언스
  게이트(mandate 위임장 규칙). `tests/integration/test_execution_tick.py`는
  이 게이트를 로컬 `_allow_gate` 대역으로 의도적으로 우회한다(그 파일
  자체 주석 참조) — 이 파일은 `run_execution_tick`에 실제 프로덕션 게이트를
  배선해 그 우회로 가려진 경로를 실제로 통과시킨다.
- I-01(게이트 항상 필수)/I-10(fail-closed 기본 자세) — 킬스위치 DENY가
  Executor/거래소 호출보다 먼저 일어남(Test 2), 거래소 거부가 조용히
  삼켜지지 않고 UNKNOWN으로 fail-closed 귀결됨(Test 3)으로 직접 검증.
- FA-*(positions 정산 SSOT, `docs/specs/L4_market_data_positions_ledger_
  v1.0.md`) — 체결이 실제 `positions` 행으로 반영됨(Test 1).
- FD-4.3(`src/services/order_service/cancel.py`)/L4-08(outbox 저장소)/
  L4-09(자기루프 전이 골격)/L4-14(`outbox_dispatcher`/`outbox_commands`
  — `send_cancel`) — 취소 요청이 실제 outbox 경계를 넘어 실제 거래소
  어댑터의 `cancel_order()`까지 도달함(Test 4).

**의도적으로 다루지 않는 것들(중복 회피)**:
- CANCELLED/EXPIRED의 최종 확정(거래소-진실 재동기화)은 이 저장소에서
  유일하게 `recovery_wiring.run_startup_recovery`만 수행한다(inbox는
  체결 이벤트만 처리 — `inbox_processor.py` 자체 주석 "이 리프는 체결
  이벤트만 다룬다" 참조) — `tests/e2e/test_restart_recovery.py`가 이미
  이 경로를 처음부터 끝까지(리스 회수 -> 거래소 재조회 -> CANCELLED 확정
  -> audit_log -> 이벤트 발행) e2e로 커버한다. 이 파일의 Test 4는 그
  앞단, 즉 취소 명령이 outbox를 통해 실제 거래소 어댑터까지 도달하는
  경계만 증명하고, CANCELLED 확정 자체는 재검증하지 않는다.
- EXPIRED는 N/A(state-machine 전이 자체는 `tests/unit/oms/
  test_state_machine.py`/`tests/integration/oms/test_cancel_modify_
  deepen.py`가 이미 커버하고, 확정 메커니즘은 CANCELLED와 완전히 동일한
  재동기화 경로를 공유해 `test_restart_recovery.py`가 이미 e2e로
  커버한다 — 별도 재현은 순수 중복이다).
- 킬스위치 자체의 우회 불가능성(무력화 변조 테스트 포함)은
  `tests/e2e/test_kill_switch_blocks_submission.py`가 이미 커버한다 —
  이 파일의 Test 2는 그 게이트가 `run_execution_tick` 경로에도 동일하게
  배선돼 있음만 추가로 증명한다(다른 진입점, 같은 게이트).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.core.executor.executor import Executor
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.safety.data_distrust import DataDistrustMonitor
from src.core.strategy.engine import StrategyEngine
from src.data.models.trading import AccountBalance, Order, OrderStatus
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.tick import run_execution_tick
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.outbox_commands import CommandCounters, send_cancel
from src.services.oms.application.outbox_writes import OutboxWrites
from src.services.order_service.cancel import cancel_order
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


def _engines(pool: asyncpg.Pool, *, require_mandate: bool = False):
    return {
        "strategy_engine": StrategyEngine(),
        "portfolio_engine": PortfolioEngine(),
        "risk_engine": RiskEngine(load_risk_policy()),
        "executor": Executor(),
        "equity_tracker": ExecutionEquityTracker(),
        "policy": load_risk_policy(),
        "pre_submit_gate": make_foundation_pre_submit_gate(pool, require_mandate=require_mandate),
        "distrust_monitor": DataDistrustMonitor(),
    }


def _bitget_adapter(
    *, place_order_result_status: OrderStatus, on_place_order=None
) -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        exchange_name="bitget",
        is_paper_trading=True,
        place_order_result_status=place_order_result_status,
        on_place_order=on_place_order,
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


async def test_signal_generates_order_that_fills_and_settles_into_position(
    pool: asyncpg.Pool,
) -> None:
    """happy path — SMA 진입 신호 -> 실 컴플라이언스 게이트 ALLOW -> 실
    거래소(대역) 체결 -> positions/ledger에 Decimal-exact 반영."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED)

    await run_execution_tick(pool, adapter, execution_id, **_engines(pool))

    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        order = await conn.fetchrow(
            "SELECT status, quantity FROM orders WHERE execution_id = $1", execution_id
        )
        position = await conn.fetchrow(
            "SELECT quantity, average_entry_price FROM positions WHERE execution_id = $1",
            execution_id,
        )
        decision_count = await conn.fetchval(
            "SELECT count(*) FROM risk_decision WHERE execution_ref = $1", f"exec:{execution_id}"
        )
    assert execution["fsm_state"] == "HOLDING"
    assert order["status"] == "FILLED"
    # allocated_capital(1000) / current_price(50) = 20
    assert order["quantity"] == Decimal("20.0000000000")
    assert position is not None
    assert position["quantity"] == Decimal("20.0000000000")
    assert position["average_entry_price"] == Decimal("50")
    assert decision_count >= 1  # R-32/R-36 사전거래 게이트가 WORM에 기록했다


async def test_kill_switch_denies_at_compliance_layer_before_any_exchange_call(
    pool: asyncpg.Pool,
) -> None:
    """failure injection #1 — 킬스위치 ACTIVE면 `run_execution_tick` 경로에서도
    실 컴플라이언스 게이트(1층, fence/kill-switch)가 Executor/거래소 호출보다
    먼저 DENY한다: fsm_state는 PENDING에 갇히지 않고 IDLE로 남는다
    (tick.py의 문서화된 전이 순서 — PENDING 전이는 게이트 통과 *직후*에만
    일어난다)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED)

    risk_repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="E2E-1 — kill switch가 tick 경로 제출을 막는다",
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines(pool))

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert fsm_state == "IDLE"
    assert order_count == 0


async def test_exchange_rejection_is_fail_closed_and_propagates_not_swallowed(
    pool: asyncpg.Pool,
) -> None:
    """failure injection #2 — 거래소가 잔고부족으로 거부하면 `Executor.
    execute()`는 예외를 삼키지 않고 그대로 재던지며(문서화된 동작 — 반복
    실패는 Watchdog가 감지), claim 행은 fail-closed로 UNKNOWN에 귀결되고
    positions에는 아무것도 안 남는다. fsm_state는 되돌려지지 않고
    BUY_ORDER_PENDING에 남는다(Executor 자신의 명시된 정책)."""

    async def reject_insufficient_funds(_order: Order) -> Order:
        raise ExchangeError(ExchangeErrorKind.INSUFFICIENT_FUNDS, venue="bitget")

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _bitget_adapter(
        place_order_result_status=OrderStatus.FILLED, on_place_order=reject_insufficient_funds
    )

    with pytest.raises(ExchangeError):
        await run_execution_tick(pool, adapter, execution_id, **_engines(pool))

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        order = await conn.fetchrow(
            "SELECT status, quantity FROM orders WHERE execution_id = $1", execution_id
        )
        position = await conn.fetchval(
            "SELECT 1 FROM positions WHERE execution_id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"
    assert order["status"] == "UNKNOWN"
    assert order["quantity"] == Decimal("20.0000000000")
    assert position is None


async def test_cancel_request_crosses_real_outbox_boundary_to_exchange_adapter(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel leg — ACKNOWLEDGED로 끝난 주문에 실 `order_service.cancel.
    cancel_order()`(자기루프 전이 + outbox CANCEL enqueue)를 호출한 뒤,
    실 `OutboxRepository.claim_batch` + `send_cancel`(L4-14)로 그 명령을
    실제 거래소 어댑터(대역)의 `cancel_order()`까지 보낸다. CANCELLED
    최종 확정은 이 저장소에서 재동기화 경로(`test_restart_recovery.py`가
    이미 e2e 커버)에서만 일어나므로 여기서는 재확인하지 않는다 — outbox
    경계가 실제로 거래소 어댑터까지 관통함만 증명한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.ACKNOWLEDGED)

    await run_execution_tick(pool, adapter, execution_id, **_engines(pool))

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow(
            "SELECT order_id, status, exchange_order_id FROM orders WHERE execution_id = $1",
            execution_id,
        )
    assert order_row["status"] == "ACKNOWLEDGED"
    assert order_row["exchange_order_id"] is not None
    order_id: UUID = order_row["order_id"]

    cancel_calls: list[str] = []
    original_cancel = adapter.cancel_order

    async def _tracked_cancel(exchange_order_id: str) -> bool:
        cancel_calls.append(exchange_order_id)
        return await original_cancel(exchange_order_id)

    monkeypatch.setattr(adapter, "cancel_order", _tracked_cancel)

    persisted = await cancel_order(order_id, adapter=adapter, pool=pool)
    assert persisted.status is OrderStatus.ACKNOWLEDGED  # CANCEL_REQUESTED는 자기루프

    worker_id = "e2e-1-cancel-worker"
    async with pool.acquire() as conn, conn.transaction():
        claimed = await OutboxRepository().claim_batch(
            conn, worker_id=worker_id, limit=10, lease_sec=30
        )
    claimed = [row for row in claimed if row.order_id == order_id]
    assert len(claimed) == 1
    assert claimed[0].command_type == "CANCEL"

    async def _resolve_adapter(_tenant_id: UUID, _exchange: str) -> FakeExchangeAdapter:
        return adapter

    writes = OutboxWrites(
        outbox_repo=OutboxRepository(), order_repo=PostgresOrderRepository(), worker_id=worker_id
    )
    counters = CommandCounters()
    await send_cancel(
        pool=pool,
        writes=writes,
        orders=PostgresOrderRepository(),
        resolve_adapter=_resolve_adapter,
        row=claimed[0],
        counters=counters,
    )

    assert cancel_calls == [order_row["exchange_order_id"]]
    assert counters.completed == 1
    async with pool.acquire() as conn:
        outbox_state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE id = $1", claimed[0].id
        )
    assert outbox_state == "DONE"
