"""R-56 적대적 — I8: tenant A의 한도·통제·결정은 tenant B의 평가에 적용되거나
조회되지 않는다.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §4.1 I8("모든 결정·한도·통제·
신호 조회는 tenant_id 조건 포함"), §8 적대적 "교차 테넌트 한도·결정·통제
조회 0건", §9 R-56(선행 R-40 `2ffd22a` "타 테넌트 미영향").

공격자 모델: tenant A(운영자 아님)가 자기 권한으로 만들 수 있는 것 —
자기 한도 행(scope_ref를 B의 식별자·B와 같은 심볼로 지정), 자기 범위 kill
switch, 자기 결정 — 로 B를 제약하거나 B의 상태를 읽으려 한다.

`check_exposure_limits`(순수)는 `ExposureLimit`에 tenant가 없어 scope_ref만
매칭한다 — 같은 심볼의 A 한도가 B의 inputs에 섞이면 B가 DENY된다는 것을
먼저 실증한 뒤(`test_..._leak_would_deny`), 저장소 `list_effective`의
`tenant_id` 조건이 그 유일한 방벽으로서 실제로 막고 있음을 6개 scope 전부에
대해 단언한다. 이 두 테스트는 한 쌍이다 — 앞의 것이 없으면 뒤의 것은
"막을 필요가 없는 것을 막았다"일 수 있다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.limits import ExposureLimit, check_exposure_limits
from src.core.risk.limits import LimitMetric as CoreMetric
from src.core.risk.limits import LimitScope as CoreScope
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_limit_repository import PostgresLimitRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import (
    UnauthorizedSafetyControlScopeError,
    activate_safety_control,
)
from src.foundation.risk_gate.application.upsert_risk_limit import (
    CrossTenantLimitScopeError,
    LimitActor,
    upsert_risk_limit,
)
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import LimitMetric, LimitScope, RiskLimit, SafetyScope
from tests.adversarial.risk.conftest import insert_decision
from tests.integration.conftest import create_test_user

_NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)
_PROVIDER = "bitget"
_SYMBOL = "BTC/USDT"
_STRATEGY = "strat-shared"
_EXEC_REF = "exec:1"


@pytest.fixture
async def tenant_a(pool: asyncpg.Pool) -> UUID:
    return await create_test_user(pool)


@pytest.fixture
async def tenant_b(pool: asyncpg.Pool) -> UUID:
    return await create_test_user(pool)


@pytest.fixture
def limit_repo(pool: asyncpg.Pool) -> PostgresLimitRepository:
    return PostgresLimitRepository(pool)


@pytest.fixture
def gate_repo(pool: asyncpg.Pool) -> PostgresRiskGateRepository:
    return PostgresRiskGateRepository(pool)


def _limit(
    tenant_id: UUID | None,
    scope: LimitScope,
    scope_ref: str,
    metric: LimitMetric = LimitMetric.MAX_ORDER_NOTIONAL,
    value: str = "1",
) -> RiskLimit:
    return RiskLimit(
        id=uuid4(), tenant_id=tenant_id, scope=scope, scope_ref=scope_ref,
        metric=metric, limit_value=Decimal(value), hard=True,
    )


def _as_exposure(limit: RiskLimit) -> ExposureLimit:
    """조립기(R-27)가 저장소 행을 순수 규칙 입력으로 바꾸는 것과 같은 변환."""
    return ExposureLimit(
        scope=CoreScope(limit.scope.value), scope_ref=limit.scope_ref,
        metric=CoreMetric(limit.metric.value), limit_value=limit.limit_value,
        hard=limit.hard, limit_id=limit.id,
    )


def _inputs(tenant_id: UUID, limits: tuple[ExposureLimit, ...]) -> RiskInputs:
    return RiskInputs(
        tenant_id=tenant_id, execution_ref=_PROVIDER, certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=OrderIntent(
            symbol=_SYMBOL, asset_class="CRYPTO_SPOT", side="BUY", quantity=Decimal("0.1"),
            ref_price=Decimal("50000"), notional=Decimal("5000"), reduce_only=False,
            strategy_id=_STRATEGY, strategy_version="1.0", capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(total_equity=Decimal("10000"), as_of=_NOW),
        exposure=ExposureSnapshot(position_quantity=Decimal("0"), as_of=_NOW),
        stats=StatsInputs(as_of=_NOW), activity=ActivityInputs(), safety=SafetyInputs(),
        limits=limits, as_of=_NOW,
    )


def _poison_limits_from(attacker: UUID, target: UUID) -> tuple[RiskLimit, ...]:
    """A가 자기 tenant 소유로 만들 수 있는, scope_ref가 B의 주문에 정확히 매칭되는
    6개 scope 한도 — 값 1(=사실상 모든 주문 거부)."""
    return (
        _limit(attacker, LimitScope.TENANT, str(target)),
        _limit(attacker, LimitScope.ACCOUNT, str(target)),
        _limit(attacker, LimitScope.STRATEGY, _STRATEGY),
        _limit(attacker, LimitScope.SYMBOL, _SYMBOL),
        _limit(attacker, LimitScope.ASSET_CLASS, "CRYPTO_SPOT"),
        _limit(attacker, LimitScope.PROVIDER, _PROVIDER),
    )


async def _effective(repo: PostgresLimitRepository, tenant_id: UUID) -> tuple[RiskLimit, ...]:
    return await repo.list_effective(
        tenant_id, provider_code=_PROVIDER, strategy_id=_STRATEGY, symbols=(_SYMBOL,)
    )


# --- 한도 --------------------------------------------------------------------


async def test_rsk_i8_tenant_a_limit_leak_would_deny_tenant_b(limit_repo, tenant_a, tenant_b):
    """방벽이 없다면 어떻게 되는가: A의 한도가 B의 inputs에 섞이면 B는 DENY다.
    이 사실이 아래 `list_effective` 격리 단언을 의미 있게 만든다."""
    poison = _poison_limits_from(tenant_a, tenant_b)
    leaked = _inputs(tenant_b, tuple(_as_exposure(limit) for limit in poison))
    result = check_exposure_limits(leaked, leaked.limits)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code is not None and result.reason_code.startswith("RISK_LIMIT_BREACH:")


async def test_rsk_i8_list_effective_for_b_returns_none_of_a_rows_in_any_scope(
    limit_repo, tenant_a, tenant_b
):
    poison = _poison_limits_from(tenant_a, tenant_b)
    for limit in poison:
        await limit_repo.upsert(limit)
    poison_ids = {limit.id for limit in poison}

    seen_by_b = {limit.id for limit in await _effective(limit_repo, tenant_b)}
    assert seen_by_b & poison_ids == set(), "A의 한도가 B의 조회에 섞였다(I8 위반)"

    # A 자신의 조회에서도 scope_ref=B인 TENANT/ACCOUNT 행은 매칭되지 않는다 —
    # A가 자기 이름으로 만든 '남을 겨냥한' 행은 어디에도 적용되지 않는 죽은 행이다.
    seen_by_a = {limit.id for limit in await _effective(limit_repo, tenant_a)}
    assert seen_by_a & poison_ids == {limit.id for limit in poison[2:]}


async def test_rsk_i8_evaluation_for_b_is_unaffected_by_a_hard_limit_on_same_symbol(
    limit_repo, tenant_a, tenant_b
):
    """저장소 → 순수 규칙까지 이어 붙인 실제 평가 경로. 같은 심볼·전략에 A만
    한도를 걸었을 때 A는 DENY, B는 ALLOW."""
    await limit_repo.upsert(_limit(tenant_a, LimitScope.SYMBOL, _SYMBOL))

    limits_b = tuple(_as_exposure(limit) for limit in await _effective(limit_repo, tenant_b))
    limits_a = tuple(_as_exposure(limit) for limit in await _effective(limit_repo, tenant_a))

    b_result = check_exposure_limits(_inputs(tenant_b, limits_b), limits_b)
    a_result = check_exposure_limits(_inputs(tenant_a, limits_a), limits_a)
    assert b_result.outcome == RiskOutcome.ALLOW, b_result
    assert a_result.outcome == RiskOutcome.DENY, a_result  # 대조군: 한도는 진짜 작동한다


async def test_rsk_i8_risk_officer_of_a_cannot_plant_limit_in_b(
    pool, limit_repo, tenant_a, tenant_b
):
    officer_a = LimitActor(subject_id=tenant_a, is_risk_officer=True)
    planted = _limit(tenant_b, LimitScope.SYMBOL, _SYMBOL)
    with pytest.raises(CrossTenantLimitScopeError):
        await upsert_risk_limit(
            limit_repo, PostgresAuditEventRepository(pool),
            tenant_id=tenant_a, actor=officer_a, limit=planted,
        )
    assert all(limit.id != planted.id for limit in await _effective(limit_repo, tenant_b))
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM risk_limit WHERE id = $1", planted.id) == 0


# --- 통제(kill switch)·fence ------------------------------------------------


async def _b_view(
    gate_repo: PostgresRiskGateRepository, tenant_b: UUID
) -> tuple[dict[tuple[SafetyScope, str], int], tuple[UUID, ...]]:
    fence, controls = await gate_repo.read_fence_and_controls(
        fence_pairs_for(tenant_b, _PROVIDER, _EXEC_REF)
    )
    return dict(fence.tokens), tuple(control.id for control in controls)


@pytest.mark.parametrize(
    ("scope", "is_admin"),
    [(SafetyScope.TENANT, True), (SafetyScope.ACCOUNT, False)],
    ids=["tenant_by_operator", "account_self_service"],
)
async def test_rsk_i8_tenant_a_kill_switch_does_not_reach_tenant_b(
    gate_repo, tenant_a, tenant_b, scope, is_admin
):
    """TENANT 범위는 운영자만(`_SELF_SERVICE_SCOPES`={ACCOUNT}) — 두 경로 모두
    A를 겨냥한 통제가 B의 fence·control 조회에 나타나지 않아야 한다."""
    b_fence_before, b_controls_before = await _b_view(gate_repo, tenant_b)

    control = await activate_safety_control(
        gate_repo, tenant_id=tenant_a, actor_subject_id=tenant_a, actor_is_admin=is_admin,
        scope=scope, scope_ref=str(tenant_a), reason="rsk-i8", trace_id=uuid4(),
    )

    b_fence_after, b_controls_after = await _b_view(gate_repo, tenant_b)
    assert b_fence_after == b_fence_before, "A의 kill switch가 B의 fence를 움직였다"
    assert b_controls_after == b_controls_before
    active_for_b = {c.id for c in await gate_repo.list_active_controls(tenant_id=tenant_b)}
    active_for_a = {c.id for c in await gate_repo.list_active_controls(tenant_id=tenant_a)}
    assert control.id not in active_for_b
    assert control.id in active_for_a


@pytest.mark.parametrize("scope", [SafetyScope.TENANT, SafetyScope.ACCOUNT])
async def test_rsk_i8_tenant_a_cannot_aim_kill_switch_at_tenant_b(
    gate_repo, tenant_a, tenant_b, scope
):
    b_fence_before, b_controls_before = await _b_view(gate_repo, tenant_b)
    with pytest.raises(UnauthorizedSafetyControlScopeError):
        await activate_safety_control(
            gate_repo, tenant_id=tenant_a, actor_subject_id=tenant_a, actor_is_admin=False,
            scope=scope, scope_ref=str(tenant_b), reason="hijack", trace_id=uuid4(),
        )
    assert await _b_view(gate_repo, tenant_b) == (b_fence_before, b_controls_before)


# --- 결정 --------------------------------------------------------------------


async def test_rsk_i8_decisions_are_listed_per_tenant_only(pool, tenant_a, tenant_b):
    repo = PostgresDecisionRepository(pool)
    a_decision = await insert_decision(pool, tenant_a)
    b_decision = await insert_decision(pool, tenant_b)

    seen_by_b = {d.decision_id for d in await repo.list_recent(tenant_b, limit=100)}
    assert b_decision.decision_id in seen_by_b
    assert a_decision.decision_id not in seen_by_b
