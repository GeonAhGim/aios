"""task-1762 — `submit.py`(실제 배포 경로)의 pre_submit_gate 거부 배선을
적대적으로 증명한다.

감사 2026-09-06 P2 — 같은 계약(R-37)의 `fenced_submit.py`는
`tests/integration/risk/test_fenced_submit.py`(test_missing_decision_id_
is_fail_closed 등)로 이미 검증됐지만, 실제 배포 경로인 `submit.py`에는
동등한 적대적 테스트가 없었다. `submit.py`는 `gate.py`와 마찬가지로
foundation을 모르는 순수 계약(`PreSubmitGate` 콜러블)만 신뢰하므로, 이
파일은 그 계약 경계에서(가짜 게이트로 "결정 없음"/"만료된 결정"을 직접
구성) 검증하고, 킬스위치 시나리오만 실제 프로덕션 게이트
(`make_foundation_pre_submit_gate`)로 배선까지 관통해 증명한다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.risk.ports.notifier import NotifyResult, PersonalNotification
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import (
    GateDecision,
    GateOutcome,
    PersonalOrderRiskSnapshot,
)
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.adversarial.risk.conftest import RecordingAdapter, make_order, seed_execution
from tests.integration.conftest import create_test_tenant


async def _count_by_client_id(pool: asyncpg.Pool, client_order_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM orders WHERE client_order_id = $1", client_order_id
        )


def _static_gate(decision: GateDecision):
    async def gate(_context) -> GateDecision:
        return decision

    return gate


async def test_missing_risk_decision_id_denies_submit_and_no_adapter_call(
    pool: asyncpg.Pool,
) -> None:
    """negative — 게이트가 "결정 없음"(risk_decision_id=None)으로 DENY하면
    submit.py 경로는 거부하고 거래소를 전혀 부르지 않는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    denied = GateDecision(
        outcome=GateOutcome.DENY,
        reason_codes=("RISK_DECISION_MISSING",),
        decision_id=None,
    )

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=_static_gate(denied),
        )

    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_expired_decision_denies_submit_and_no_adapter_call(pool: asyncpg.Pool) -> None:
    """negative — 게이트가 만료된 결정으로 DENY하면 submit.py 경로는 거부하고
    거래소를 전혀 부르지 않는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    denied = GateDecision(
        outcome=GateOutcome.DENY,
        reason_codes=("RISK_DECISION_EXPIRED",),
        decision_id=uuid4(),
    )

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=_static_gate(denied),
        )

    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_active_kill_switch_denies_submit_via_real_production_gate(
    pool: asyncpg.Pool,
) -> None:
    """킬스위치(ACCOUNT scope) 활성 상태에서 실제 프로덕션 게이트 조립부
    (`make_foundation_pre_submit_gate`)를 그대로 주입한 submit.py 경로가
    거부되고 거래소를 전혀 부르지 않음을 증명한다(우회불가능성, I-10)."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()

    await activate_safety_control(
        PostgresRiskGateRepository(pool),
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=True,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="task-1762 적대적 테스트 — submit.py 킬스위치 배선",
    )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as excinfo:
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=gate,
        )

    assert any("RISK_KILL_SWITCH_ACTIVE" in code for code in excinfo.value.reason_codes)
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[PersonalNotification] = []

    async def send(self, notification: PersonalNotification) -> NotifyResult:
        self.sent.append(notification)
        return NotifyResult(ok=True, status_code=200, error=None)


class _FakePersonalState:
    """task-3986 — `PersonalOperationStatePort` test double, scoped to a
    single `account_id` (or `None`, meaning personal mode is not configured
    for any account -- mirrors `JsonPersonalStateStore.personal_mode_
    account_id()`'s "env var unset" case)."""

    def __init__(self, account_id: UUID | None) -> None:
        self._account_id = account_id
        self.violations: list[date] = []
        self.kill_engaged = False
        self.kill_reason_value: str | None = None
        self.paper_started: date | None = None

    async def personal_mode_account_id(self) -> UUID | None:
        return self._account_id

    async def is_kill_engaged(self) -> bool:
        return self.kill_engaged

    async def kill_reason(self) -> str | None:
        return self.kill_reason_value

    async def engage_kill(self, *, reason: str) -> None:
        self.kill_engaged = True
        self.kill_reason_value = reason

    async def mark_paper_started_if_unset(self, *, today: date) -> date:
        if self.paper_started is None:
            self.paper_started = today
        return self.paper_started

    async def record_violation(self, *, occurred_on: date) -> None:
        self.violations.append(occurred_on)

    async def violation_count_since(self, since: date) -> int:
        return sum(1 for v in self.violations if v >= since)

    async def violation_count_on(self, day: date) -> int:
        return sum(1 for v in self.violations if v == day)


def _personal_snapshot(*, symbol: str = "BTC/USDT") -> PersonalOrderRiskSnapshot:
    """`config/risk_policy/personal-conservative.yaml`'s real
    `symbol_whitelist` is empty (fail-closed default, docs/ops/
    PERSONAL_MODE.md §1) -- any symbol violates SYMBOL_NOT_WHITELISTED
    regardless of the other numeric thresholds, so this snapshot's exact
    notional/equity values are deliberately unremarkable (well inside every
    other limit) to prove the whitelist violation alone drives the DENY."""
    return PersonalOrderRiskSnapshot(
        symbol=symbol,
        order_notional_krw=Decimal("10000"),
        account_equity_krw=Decimal("1000000"),
        current_exposure_krw=Decimal("0"),
        daily_realized_pnl_pct=Decimal("0"),
    )


async def test_personal_mode_scoped_account_order_denied_via_real_submit_gate(
    pool: asyncpg.Pool,
) -> None:
    """task-3986 (QA task-3819 결함 수정) — personal 모드가 스코프된 계정이
    화이트리스트에 없는 심볼을 실제 `submit_order` 경로(모의 게이트가 아닌
    `make_foundation_pre_submit_gate` 프로덕션 조립부)로 제출하면 4층에서
    DENY된다. 기존 `tests/foundation/adversarial/risk/
    test_personal_bundle_adversarial.py`는 `check_personal_order`를 직접
    호출하는 함수 단위 테스트라 "실주문 경로에 배선 안 됨" 결함을 잡지
    못했다 -- 이 테스트는 반드시 `foundation_gate.py`를 경유한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    personal_state = _FakePersonalState(account_id=user_id)
    notifier = _FakeNotifier()

    gate = make_foundation_pre_submit_gate(
        pool,
        require_mandate=False,
        personal_state=personal_state,
        personal_notifier=notifier,
    )

    with pytest.raises(OrderDeniedByRiskGateError) as excinfo:
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=gate,
            personal_risk_snapshot=_personal_snapshot(),
        )

    assert "SYMBOL_NOT_WHITELISTED" in excinfo.value.reason_codes
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0
    assert len(personal_state.violations) == 1
    assert any(n.kind.value == "LIMIT_BREACH" for n in notifier.sent)


async def test_personal_mode_inactive_account_regresses_to_existing_three_layer_flow(
    pool: asyncpg.Pool,
) -> None:
    """negative — personal 모드가 스코프되지 않은(또는 다른 계정에 스코프된)
    계정은 4층이 아예 평가되지 않고, 기존 3층 게이트 동작(mandate 없음 +
    require_mandate=False -> ALLOW)이 회귀 없이 그대로 통과한다. `personal_
    risk_snapshot`을 넘기지 않아도(프로덕션 호출부 전부의 현재 상태) 영향
    없다 -- 전역 강제 금지(item 1)의 직접 증명."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    # scoped to a *different* account -- proves the check is account-scoped,
    # not merely "personal_state happens to be wired".
    personal_state = _FakePersonalState(account_id=uuid4())

    gate = make_foundation_pre_submit_gate(
        pool,
        require_mandate=False,
        personal_state=personal_state,
    )

    result = await submit_order(
        order,
        user_id=user_id,
        adapter=adapter,
        pool=pool,
        pre_submit_gate=gate,
    )

    assert result.exchange_order_id is not None
    assert adapter.place_order_call_count == 1
    assert personal_state.violations == []


async def test_personal_mode_kill_switch_already_engaged_denies_without_needing_snapshot(
    pool: asyncpg.Pool,
) -> None:
    """negative — docs/ops/PERSONAL_MODE.md §4 "일일 손실 한도 초과 시에도
    같은 스위치가 자동으로 켜지고... 모든 신규 주문이 막힌다"는 약속의 직접
    증명: kill switch가 이미 켜져 있으면(예: 이전 주문에서 일일 손실 한도
    위반으로 자동 발동) `personal_risk_snapshot` 없이도(수치 데이터를 몰라도)
    스코프된 계정의 신규 주문은 즉시 거부된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    personal_state = _FakePersonalState(account_id=user_id)
    personal_state.kill_engaged = True
    personal_state.kill_reason_value = "DAILY_LOSS_LIMIT_BREACHED"

    gate = make_foundation_pre_submit_gate(
        pool,
        require_mandate=False,
        personal_state=personal_state,
    )

    with pytest.raises(OrderDeniedByRiskGateError) as excinfo:
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=gate,
        )

    assert excinfo.value.reason_codes == ("RISK_PERSONAL_KILL_SWITCH_ENGAGED",)
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0
