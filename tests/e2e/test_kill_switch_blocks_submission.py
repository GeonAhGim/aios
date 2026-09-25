"""H-7a 백엔드 e2e #2 — kill switch(ACTIVE) 중 주문 제출 거부, 풀스택.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-7
("백엔드 e2e 3건: ... kill switch 중 거부 ...").
Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §8
("safety_control을 ACTIVE로 만든 뒤 ... 신규 주문 제출이 시도조차 되지
않음을 증명"). docs/design/INVARIANTS.md I-10(fail-closed).

`tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py`는
`ExecutionLoopScheduler.tick_all_running()` 경로에서 이를 증명한다 — 이
e2e는 그보다 한 층 아래, `submit_order()` 직접 호출 경로에서 실
Postgres(TEST_DATABASE_URL) + 그 파일과 동일한 "paper 어댑터" 대역
(`is_paper_trading=True`인 `FakeExchangeAdapter` — `PaperSimulatorAdapter`를
쓰지 않는 이유는 test_order_execution_settlement.py 모듈 docstring 참조:
그 클래스는 `submit_order()`의 "어댑터가 order_id를 보존한다" 계약을
어겨 이 파이프라인에 아직 배선될 수 없다)로 같은 배선을 증명하고, kill
switch가 없으면 같은 주문이 실제로 체결까지 감을 대조군으로 확인한다
(단순히 "언제나 거부"하는 깨진 게이트가 아님을 증명).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import FenceSnapshot, SafetyControl, SafetyScope
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.foundation.execution_ownership.conftest import create_execution


def _make_adapter() -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        exchange_name="paper_sim",
        is_paper_trading=True,
        place_order_result_status=OrderStatus.FILLED,
        closes=[Decimal("30000")] * 30,
        usdt_balance=AccountBalance(
            exchange="paper_sim", asset="USDT", total=Decimal("100000"), available=Decimal("100000")
        ),
    )


def _make_order(execution_id: int, client_order_id_label: str) -> Order:
    # uuid 접미사 격리(docs/TESTING.md 관례) — 재실행 시 UNIQUE 충돌 방지.
    return Order(
        client_order_id=f"{client_order_id_label}-{uuid4().hex[:8]}",
        strategy_id="e2e-kill-switch",
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


async def test_active_kill_switch_denies_order_submission_end_to_end(pool: asyncpg.Pool) -> None:
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    adapter = _make_adapter()

    risk_repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="H-7a e2e — kill switch 제출 거부",
    )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _make_order(execution_id, "kill-switch-deny")

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate)

    assert exc_info.value.reason_codes == ("RISK_KILL_SWITCH_ACTIVE_ACCOUNT",)
    assert adapter.place_order_call_count == 0  # 게이트가 claim/거래소 호출보다 먼저 막았다

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert exists is None  # 거부된 시도는 orders에 흔적을 남기지 않는다


async def test_without_active_kill_switch_same_order_is_allowed_and_fills(
    pool: asyncpg.Pool,
) -> None:
    """대조군 — 위 테스트의 DENY가 "항상 거부하는 깨진 게이트" 때문이
    아니라 kill switch 활성화 자체 때문임을, 같은 설정에서 kill switch만
    빼고 재현해 실제 체결까지 감을 보여 증명한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    adapter = _make_adapter()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _make_order(execution_id, "kill-switch-control")

    submitted = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate
    )

    assert submitted.status is OrderStatus.FILLED


async def test_disabling_kill_switch_check_would_let_order_through(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """변조 케이스(실패 주입) — kill switch는 여전히 ACTIVE인 채로, 게이트가
    active_controls를 읽는 지점만 몽키패치로 무력화하면(마치 그 검사가
    배선에서 빠진 결함처럼) 첫 번째 테스트가 증명한 거부가 사라지고 주문이
    실제로 체결까지 감을 보여준다 — 즉, 이 무력화가 있으면 위 첫 테스트의
    "place_order_call_count == 0" 단언이 실제로 FAIL한다는 것을 증명한다
    (이 e2e가 우회 불가능성을 실제로 감시하고 있다는 근거)."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    risk_repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="H-7a e2e — 변조 대조군(게이트 무력화)",
    )

    original_read = PostgresRiskGateRepository.read_fence_and_controls

    async def blind_to_active_controls(
        self: PostgresRiskGateRepository, pairs: tuple[tuple[SafetyScope, str], ...]
    ) -> tuple[FenceSnapshot, tuple[SafetyControl, ...]]:
        fence_snapshot, _active_controls = await original_read(self, pairs)
        return fence_snapshot, ()  # kill switch가 활성인데도 못 본 척(주입한 결함)

    monkeypatch.setattr(
        PostgresRiskGateRepository, "read_fence_and_controls", blind_to_active_controls
    )

    adapter = _make_adapter()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _make_order(execution_id, "kill-switch-mutation")

    submitted = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate
    )

    # 실제로 무력화되면 체결까지 간다 — 이것이 바로 첫 테스트가 잡아야 할
    # 결함(우회)이다.
    assert submitted.status is OrderStatus.FILLED
