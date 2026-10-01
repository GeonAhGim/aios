"""task-9065(F4(L), 안정화 감사 task-8882) — insert_safety_control()이 같은
(scope, scope_ref)에 대해 ACTIVE 행을 중복 생성하지 않는지 확인.

차단 판정(`list_active_controls`의 존재 여부 체크)에는 영향이 없었지만,
중복 ACTIVE 행이 쌓이면 fence 토큰/감사흔적이 오염될 수 있다는 게 audit
F4(L)의 지적이다. `PostgresRiskGateRepository.insert_safety_control()`은
이제 INSERT 전에 같은 (scope, scope_ref)의 ACTIVE 행을 `SELECT ... FOR
UPDATE`로 조회해, 있으면 `ConcurrencyConflictError`를 던진다.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.risk_gate.application.activate_safety_control import (
    ensure_safety_control_active,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import SafetyControlAlreadyActiveError
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


async def test_ensure_active_reuses_existing_control_without_spending_a_fence_token(pool, repo):
    """System escalations (unknown-order resolver, reconciliation engines) call
    `ensure_safety_control_active()`: when the scope is already ACTIVE the existing control is
    returned as-is -- no second row, no conflict, and the fence token does not move."""
    tenant_id = await _tenant(pool)
    kwargs = {
        "tenant_id": tenant_id,
        "actor_subject_id": tenant_id,
        "actor_is_admin": True,
        "scope": SafetyScope.ACCOUNT,
        "scope_ref": str(tenant_id),
    }

    first = await ensure_safety_control_active(repo, reason="first escalation", **kwargs)
    second = await ensure_safety_control_active(repo, reason="second escalation", **kwargs)

    assert second.id == first.id
    assert second.fence_token == first.fence_token
    assert second.reason == "first escalation"  # the engaged control is not rewritten
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM safety_control "
            "WHERE scope = 'ACCOUNT' AND scope_ref = $1 AND state = 'ACTIVE'",
            str(tenant_id),
        )
    assert count == 1


async def test_ensure_active_engages_a_new_control_after_the_previous_one_was_released(pool, repo):
    """After a release the scope is unprotected again -- ensure must engage a NEW control with a
    newer fence token, never hand back the released one."""
    tenant_id = await _tenant(pool)
    kwargs = {
        "tenant_id": tenant_id,
        "actor_subject_id": tenant_id,
        "actor_is_admin": True,
        "scope": SafetyScope.ACCOUNT,
        "scope_ref": str(tenant_id),
    }
    first = await ensure_safety_control_active(repo, reason="first", **kwargs)
    await repo.deactivate_safety_control(first.id)

    second = await ensure_safety_control_active(repo, reason="second", **kwargs)

    assert second.id != first.id
    assert second.fence_token > first.fence_token
    assert second.state.value == "ACTIVE"


async def test_manual_activation_still_conflicts_with_the_dedicated_error_type(pool, repo):
    """The strict path is unchanged for the manual API: a duplicate activation raises the
    dedicated subtype (still a ConcurrencyConflictError, so the 409 mapping holds) and carries
    the id of the control that is already engaged."""
    tenant_id = await _tenant(pool)
    first = await repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="manual",
        actor_subject_id=tenant_id,
    )
    with pytest.raises(SafetyControlAlreadyActiveError) as excinfo:
        await repo.insert_safety_control(
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(tenant_id),
            reason="manual again",
            actor_subject_id=tenant_id,
        )
    assert isinstance(excinfo.value, ConcurrencyConflictError)
    assert excinfo.value.control_id == first.id


async def test_ten_concurrent_first_activations_produce_exactly_one_active_control(pool, repo):
    """negative(task-10672, R-40 후속, task-10490 DEEPEN CM-12) — 같은
    (scope, scope_ref)에 ACTIVE 행이 하나도 없는 상태에서 동시에 10건의 최초
    activate가 들어오면, 과거 구현(`SELECT ... FOR UPDATE`만)은 매치되는 행이
    없어 아무것도 잠그지 못하고 전부 통과해 중복 ACTIVE 행과 fence token
    낭비가 발생했다(TOCTOU). advisory lock으로 직렬화한 뒤에는 정확히 1건만
    성공하고 나머지 9건은 `ConcurrencyConflictError`로 거부돼야 한다."""
    tenant_id = await _tenant(pool)
    scope_ref = str(tenant_id)

    async def attempt(i: int):
        return await repo.insert_safety_control(
            scope=SafetyScope.ACCOUNT,
            scope_ref=scope_ref,
            reason=f"concurrent activate #{i}",
            actor_subject_id=tenant_id,
        )

    results = await asyncio.gather(*(attempt(i) for i in range(10)), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]

    assert len(successes) == 1, f"정확히 1건만 성공해야 하는데 {len(successes)}건 성공했다"
    assert len(failures) == 9
    for failure in failures:
        assert isinstance(failure, ConcurrencyConflictError)

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM safety_control "
            "WHERE scope = 'ACCOUNT' AND scope_ref = $1 AND state = 'ACTIVE'",
            scope_ref,
        )
    assert count == 1, "동시 최초-activate 경합 후에도 ACTIVE 행은 정확히 1개여야 한다"


async def test_two_concurrent_deactivations_of_the_same_control_leave_exactly_one_winner(
    pool, repo
):
    """task-10780 A6-G5(audit §5 G5-2) 재현 — 킬스위치 비활성화에 별도의
    멱등키가 없는 것이 중복 control 행을 만들 위험으로 지적됐다.
    `deactivate_safety_control()`은 INSERT가 아니라 조건부 UPDATE(`WHERE
    id=$1 AND state='ACTIVE'`)라 애초에 새 행을 만들지 않는다 — 두 재시도가
    동시에 같은 control_id를 해제해도 Postgres 행 잠금이 자연히 직렬화해
    정확히 한 번만 성공하고, 나머지는 명시적으로 거부돼야 한다(조용한 중복
    처리나 두 번째 행 생성은 없어야 한다) — 결과: 오탐 아님/사실이지만 이미
    표준-105 조건부 UPDATE가 막고 있다(코드 변경 불필요, 이 테스트는 그
    사실의 재현 증빙)."""
    tenant_id = await _tenant(pool)
    control = await repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="동시 해제 재현",
        actor_subject_id=tenant_id,
    )

    async def attempt():
        return await repo.deactivate_safety_control(control.id)

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"정확히 1건만 성공해야 하는데 {len(successes)}건 성공했다"
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrencyConflictError)

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM safety_control WHERE id = $1", control.id)
    assert count == 1, "해제 경합이 새 control 행을 만들면 안 된다(UPDATE이지 INSERT가 아님)"


async def test_deactivating_an_already_inactive_control_is_explicitly_rejected(pool, repo):
    """task-10780 A6-G5(audit §5 G5-3) 재현 — "이미 INACTIVE인 통제에 대한
    비활성화 요청이 명시적으로 거부되는지" 확인. 결과: 오탐 아님/사실이지만
    이미 올바르게 동작한다 — `activate_safety_control()`의
    `SafetyControlAlreadyActiveError`(이미 ACTIVE인데 다시 activate)와
    대칭으로, `deactivate_safety_control()`도 이미 INACTIVE인 control을
    다시 해제하려 하면 조용히 성공을 가장하지 않고 `ConcurrencyConflictError`
    로 명시적으로 거부한다(코드 변경 불필요, 이 테스트는 그 사실의 재현
    증빙)."""
    tenant_id = await _tenant(pool)
    control = await repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="이미 비활성 재현",
        actor_subject_id=tenant_id,
    )
    first = await repo.deactivate_safety_control(control.id)
    assert first.state.value == "INACTIVE"

    with pytest.raises(ConcurrencyConflictError):
        await repo.deactivate_safety_control(control.id)
