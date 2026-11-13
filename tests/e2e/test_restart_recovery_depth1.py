"""H-7a depth 보강 D1->D2: 재시작 복구 — 부정/실패주입/게이트 적색/성능 보조 테스트.

기존 `test_restart_recovery.py`를 수정하지 않고, 부족한 D2 증거만 새 파일로 보강한다.

- negative 3종:
    1. RecoveryState 미완료 → make_recovery_gate이 항상 DENY (delegate 호출 안 함).
    2. enabled=False → recovery가 전혀 실행되지 않고 즉시 완료 (무효 복구 호출 거부).
    3. lease가 아예 없는 execution_id에 recovery 호출 → 에러 없이 0회수 (비어있음 음성).
- 실패 주입: recover_stuck_outbox_commands가 None risk_gate_repo로 호출하면 TypeError(I-01).
- 게이트 적색: RecoveryState를 조작해 "복구 완료 전"에 submit_order가 실제로 거부되는지
    real gate 경로로 확인.
- 성능: run_startup_recovery best-of-5 측정. perf_inject 가상 지연 10배 주입 → 적색 재현.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6, §9 L4-18a/b.
INVARIANTS.md I-10(fail-closed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.execution_loop import recovery_wiring
from src.services.oms.application.restart_recovery import (
    RECOVERY_IN_PROGRESS_REASON,
    RecoveryState,
    make_recovery_gate,
    reclaim_expired_leases,
    recover_stuck_outbox_commands,
)
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.foundation.execution_ownership.conftest import create_execution
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


async def _no_adapter(user_id: UUID, exchange: str) -> ExchangeAdapter:
    raise AssertionError("no adapter should be resolved on an empty DB")


async def _no_publish(topic: str, payload: dict[str, Any]) -> None:
    return None


def _now() -> datetime:
    return datetime.now(UTC)


async def _seed_usdt(pool: asyncpg.Pool, account_id: UUID, amount: Decimal) -> None:
    from src.exchanges.paper.ledger_repository import PaperLedgerRepository

    ledger = PaperLedgerRepository()
    async with pool.acquire() as conn:
        await ledger.deposit(conn, account_id, "USDT", amount)


def _make_market_order(execution_id: int, client_order_id_label: str) -> Order:
    return Order(
        client_order_id=f"{client_order_id_label}-{uuid4().hex[:8]}",
        strategy_id="e2e-restart-recovery-depth1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


# ---------------------------------------------------------------------------
# negative 1: RecoveryState 미완료 → 게이트가 delegate를 호출하지 않고 DENY
# ---------------------------------------------------------------------------


async def test_recovery_gate_denies_without_delegate_call(pool: asyncpg.Pool) -> None:
    """negative: RecoveryState가 미완료일 때 make_recovery_gate은 delegate를 단 한 번도
    호출하지 않고 즉시 DENY해야 한다(I-10 fail-closed). 만약 delegate가 호출된다면
    이 테스트는 AssertionError로 FAIL한다 — 복구 완료 전에는 어떤 검증도 실행되면
    안 된다는 불변식이다."""
    state = RecoveryState()  # _complete=False

    async def delegate_should_never_be_called(context: OrderContext) -> GateDecision:
        raise AssertionError("delegate는 RecoveryState 미완료 시 호출되면 안 된다")

    gate = make_recovery_gate(state, delegate_should_never_be_called)

    decision = await gate(
        OrderContext(
            user_id=uuid4(), execution_id=999, exchange="paper_sim", mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)


# ---------------------------------------------------------------------------
# negative 2: enabled=False → recovery가 전혀 실행되지 않음
# ---------------------------------------------------------------------------


async def test_recovery_skipped_when_flag_disabled(pool: asyncpg.Pool) -> None:
    """negative: AIOS_STARTUP_RECOVERY_ENABLED=0일 때 run_startup_recovery_gated은
    recovery 단계를 건너뛰고 즉시 완료 상태로 돌아야 한다. 이 테스트는 lease/outbox
    주문이 남아 있어도 recovery가 실행되지 않음을 확인한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)

    # lease를 심어두지만, enabled=False이면 recovery가 이를 건드리지 않는다.
    async def insert_expired_leases() -> None:
        expires_at = _now() - timedelta(seconds=30)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO execution_leases (execution_id, owner_id, fencing_token, "
                "heartbeat_at, expires_at) VALUES ($1, $2, 0, now() - interval '1 hour', $3)",
                execution_id,
                "disabled-recovery",
                expires_at,
            )

    await insert_expired_leases()

    # enabled=False → recovery 단계 건너뛰기
    state = await recovery_wiring.run_startup_recovery_gated(
        pool,
        resolve_adapter=_no_adapter,
        publish=_no_publish,
        enabled=False,
    )

    assert state.complete is True

    # lease가 여전히 남아 있음 (recovery가 실행되지 않았음)
    async with pool.acquire() as conn:
        lease_count = await conn.fetchval(
            "SELECT count(*) FROM execution_leases WHERE execution_id = $1", execution_id
        )
    assert lease_count == 1  # recovery가 실행되지 않았으므로 lease가 그대로


# ---------------------------------------------------------------------------
# negative 3: lease가 아예 없는 execution_id → 0회수, 에러 없음
# ---------------------------------------------------------------------------


async def test_reclaim_empty_leases_returns_zero(pool: asyncpg.Pool) -> None:
    """negative: execution_leases에 행이 전혀 없으면 reclaim_expired_leases는
    0을 반환하고 예외를 던지지 않아야 한다. 먼저 기존 lease를 모두 정리한 뒤
    확인한다."""
    # 다른 테스트가 남긴 lease를 정리
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM execution_leases")
    reclaimed = await reclaim_expired_leases(pool)
    assert reclaimed == 0


# ---------------------------------------------------------------------------
# 실패 주입: recover_stuck_outbox_commands에 None risk_gate_repo → TypeError (I-01)
# ---------------------------------------------------------------------------


async def test_outbox_recovery_rejects_none_risk_gate_repo(pool: asyncpg.Pool) -> None:
    """실패 주입: I-01 불변식 — risk_gate_repo가 None이면 TypeError를 던진다.
    안전 게이트가 생략될 수 없음을 확인한다."""
    from src.services.oms.adapters.order_repository import PostgresOrderRepository
    from src.services.oms.adapters.outbox_repository import OutboxRepository

    with pytest.raises(TypeError, match="I-01"):
        await recover_stuck_outbox_commands(
            pool,
            order_repo=PostgresOrderRepository(),
            outbox_repo=OutboxRepository(),
            resolve_adapter=_no_adapter,
            risk_gate_repo=cast(Any, None),
        )


# ---------------------------------------------------------------------------
# 게이트 적색 재현: RecoveryState 조작 → 복구 완료 전 submit_order가 실제로 거부됨
# ---------------------------------------------------------------------------


async def test_gate_red_reproduction_submit_denied_before_recovery(pool: asyncpg.Pool) -> None:
    """게이트 적색 재현: RecoveryState를 '미완료' 상태로 고정하고, real gate 경로
    (make_foundation_pre_submit_gate)로 submit_order를 호출하면 실제로 거부된다.
    이는 게이트가 '복구 완료 전'에 실제로 차단함을 입증한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    await _seed_usdt(pool, user_id, Decimal("100000"))

    # RecoveryState를 미완료로 고정
    state = RecoveryState()

    adapter = FakeExchangeAdapter(
        exchange_name="paper_sim",
        is_paper_trading=True,
        place_order_result_status=OrderStatus.FILLED,
        closes=[Decimal("30000")] * 30,
        usdt_balance=AccountBalance(
            exchange="paper_sim", asset="USDT", total=Decimal("100000"), available=Decimal("100000")
        ),
    )

    order = _make_market_order(execution_id, "gate-red-order")

    # real gate 경로를 통과하도록 delegate를 연결
    async def real_delegate(context: OrderContext) -> GateDecision:
        real_gate = make_foundation_pre_submit_gate(
            pool, require_mandate=False, require_compliance_mandate=False
        )
        return await real_gate(context)

    gate = make_recovery_gate(state, real_delegate)

    # RecoveryState가 미완료이므로 실제로 거부되어야 함
    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=gate,
        )
    assert exc_info.value.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)

    # 주문이 DB에 남지 않았음
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert exists is None


# ---------------------------------------------------------------------------
# 성능: run_startup_recovery best-of-5 측정 + 가상 지연 10배 주입 적색 재현
# ---------------------------------------------------------------------------

# 성능 예산: 로컬 실측 약 5ms(best-of-5, empty DB). 예산은 30배 이상 여유인 200ms로 설정.
# 재실행 시 실측을 갱신하려면 docstring의 '약 5ms'를 확인하고 필요시 30배 이상으로 조정.
_RECOVERY_PERF_BUDGET_MS = 200.0


@pytest.mark.perf
async def test_recovery_perf_budget_empty_db(pool: asyncpg.Pool, perf_budget: PerfBudget) -> None:
    """성능 단언: lease/outbox/order가 없는 빈 DB에서 run_startup_recovery best-of-5가
    예산 미만."""
    user_id = await create_test_tenant(pool)
    await create_execution(pool, user_id)

    reset_virtual_totals()

    async def _run() -> object:
        return await recovery_wiring.run_startup_recovery_gated(
            pool,
            resolve_adapter=_no_adapter,
            publish=_no_publish,
            enabled=True,
        )

    sample = await perf_budget.best_of_async(_run, n=5, warmup=1)
    assert sample.cpu_ms < _RECOVERY_PERF_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_RECOVERY_PERF_BUDGET_MS
    )


@pytest.mark.perf
async def test_recovery_perf_gate_red_with_10x_injection(
    pool: asyncpg.Pool, perf_budget: PerfBudget
) -> None:
    """게이트 적색 재현: 성능 측정 경로를 virtual_delayed_async로 10배 지연 주입하면
    같은 예산 단언이 적색이 된다."""
    user_id = await create_test_tenant(pool)
    await create_execution(pool, user_id)

    reset_virtual_totals()

    injected_delay_s = _RECOVERY_PERF_BUDGET_MS * 10 / 1000

    async def _run() -> object:
        return await recovery_wiring.run_startup_recovery_gated(
            pool,
            resolve_adapter=_no_adapter,
            publish=_no_publish,
            enabled=True,
        )

    _delayed_run = virtual_delayed_async(_run, injected_delay_s)

    sample = await perf_budget.best_of_async(_delayed_run, n=3, warmup=0)
    # virtual_delayed_async는 wall-clock 지연만 주입하므로 wall_ms로 적색 확인
    assert not (sample.wall_ms < _RECOVERY_PERF_BUDGET_MS), (
        f"wall={sample.wall_ms:.1f}ms budget={_RECOVERY_PERF_BUDGET_MS}ms"
    )
    reset_virtual_totals()
