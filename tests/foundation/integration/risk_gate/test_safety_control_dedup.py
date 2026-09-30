"""task-9065(F4(L), 안정화 감사 task-8882) — insert_safety_control()이 같은
(scope, scope_ref)에 대해 ACTIVE 행을 중복 생성하지 않는지 확인.

차단 판정(`list_active_controls`의 존재 여부 체크)에는 영향이 없었지만,
중복 ACTIVE 행이 쌓이면 fence 토큰/감사흔적이 오염될 수 있다는 게 audit
F4(L)의 지적이다. `PostgresRiskGateRepository.insert_safety_control()`은
이제 INSERT 전에 같은 (scope, scope_ref)의 ACTIVE 행을 `SELECT ... FOR
UPDATE`로 조회해, 있으면 `ConcurrencyConflictError`를 던진다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.risk_gate.domain.models import SafetyScope
from tests.foundation.integration.risk_gate.conftest import _tenant


async def test_reactivating_an_already_active_scope_ref_is_explicitly_rejected(pool, repo):
    """negative — 같은 (scope, scope_ref)가 이미 ACTIVE인데 다시
    insert_safety_control()을 부르면 조용히 중복 생성하지 않고 명시적으로
    거부해야 한다."""
    tenant_id = await _tenant(pool)

    first = await repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="최초 activate",
        actor_subject_id=tenant_id,
    )
    assert first.state.value == "ACTIVE"

    with pytest.raises(ConcurrencyConflictError):
        await repo.insert_safety_control(
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(tenant_id),
            reason="중복 activate 시도",
            actor_subject_id=tenant_id,
        )

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM safety_control "
            "WHERE scope = 'ACCOUNT' AND scope_ref = $1 AND state = 'ACTIVE'",
            str(tenant_id),
        )
    assert count == 1, "거부된 시도가 그래도 ACTIVE 행을 남겨서는 안 된다"


async def test_same_scope_different_scope_ref_inserts_normally(pool, repo):
    """negative(회귀) — scope는 같지만 scope_ref가 다르면 dedup 체크에
    걸리지 않고 정상적으로 각자 독립된 ACTIVE 행을 만들어야 한다."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)

    control_a = await repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_a),
        reason="tenant A activate",
        actor_subject_id=tenant_a,
    )
    control_b = await repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_b),
        reason="tenant B activate",
        actor_subject_id=tenant_b,
    )

    assert control_a.state.value == "ACTIVE"
    assert control_b.state.value == "ACTIVE"
    assert control_a.id != control_b.id


async def test_reactivation_allowed_after_prior_control_deactivated(pool, repo):
    """negative — 거부는 "현재 ACTIVE인 행이 있을 때"만 적용된다. 이전 control이
    INACTIVE로 전환된 뒤에는 같은 (scope, scope_ref)를 다시 activate할 수
    있어야 한다(자의적 영구 차단이 아님)."""
    scope_ref = str(uuid4())

    first = await repo.insert_safety_control(
        scope=SafetyScope.PROVIDER,
        scope_ref=scope_ref,
        reason="최초 activate",
        actor_subject_id=await _tenant(pool),
    )
    await repo.deactivate_safety_control(first.id)

    second = await repo.insert_safety_control(
        scope=SafetyScope.PROVIDER,
        scope_ref=scope_ref,
        reason="재활성화",
        actor_subject_id=await _tenant(pool),
    )
    assert second.state.value == "ACTIVE"
    assert second.id != first.id
