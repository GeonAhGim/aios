"""task-9063 — 안정화 감사 2026-09-29 F1(S-tier) 재현·정정 회귀 방지.

docs/audits/AUDIT_2026-09-29_risk_circuit_breaker.md §3: circuit breaker가
HALTED/EMERGENCY로 격상돼도 그 격상 자체는 `safety_control` 행을 만들지
않으므로, `foundation_gate.py`의 PreSubmitGate 1층(활성 control만 확인)은
CB 상태를 전혀 보지 않고 ALLOW를 냈다. PRE_TRADE(tick_risk_phase.py)는
이미 막았으므로 완전한 fail-open은 아니지만, PRE_TRADE 통과~어댑터 호출
사이 TOCTOU 창에서는 이 결함이 그대로 열려 있었다.

이 파일은 감사 3절의 재현 시나리오(킬스위치 없음, fence fresh, mandate/
compliance 통과, CB만 halted/emergency로 격상) 그대로 실제 프로덕션 게이트
조립부(`make_foundation_pre_submit_gate`)를 `submit_order` 경로로 관통해
DENY를 증명하고, warning/restricted(자동 하향 가능 레벨, `CircuitBreakerLevel`
8.6-B)는 기존 동작(ALLOW)을 유지함을 회귀로 고정한다.
"""

from __future__ import annotations

import time

import asyncpg
import pytest

from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.adversarial.risk.conftest import RecordingAdapter, make_order, seed_execution
from tests.integration.conftest import create_test_tenant


async def _set_cb_level(pool: asyncpg.Pool, level: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = $1, "
            "reactivation_approval_id = NULL WHERE id = 1",
            level,
        )


async def _count_by_client_id(pool: asyncpg.Pool, client_order_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM orders WHERE client_order_id = $1", client_order_id
        )


@pytest.fixture(autouse=True)
async def _reset_cb_level(pool: asyncpg.Pool):
    await _set_cb_level(pool, "normal")
    yield
    await _set_cb_level(pool, "normal")


@pytest.mark.parametrize("level", ["halted", "emergency"])
async def test_cb_halted_or_emergency_denies_pre_submit_no_kill_switch_needed(
    pool: asyncpg.Pool, level: str
) -> None:
    """감사 재현 시나리오 그대로 — kill switch 없음, fence fresh, mandate/
    compliance 통과 상태에서 CB만 halted/emergency로 격상되면 실제 프로덕션
    PreSubmitGate가 DENY하고 거래소를 전혀 부르지 않아야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()

    await _set_cb_level(pool, level)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    with pytest.raises(OrderDeniedByRiskGateError) as excinfo:
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=gate,
        )

    assert excinfo.value.reason_codes == (f"RISK_CIRCUIT_BREAKER_{level.upper()}",)
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


@pytest.mark.parametrize("level", ["warning", "restricted"])
async def test_cb_warning_or_restricted_does_not_regress_pre_submit(
    pool: asyncpg.Pool, level: str
) -> None:
    """negative — warning/restricted(자동 하향 가능 레벨)는 이 정정의 대상이
    아니다. 기존 동작(ALLOW, 거래소 호출됨)이 회귀 없이 그대로 유지돼야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()

    await _set_cb_level(pool, level)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    result = await submit_order(
        order,
        user_id=user_id,
        adapter=adapter,
        pool=pool,
        pre_submit_gate=gate,
    )

    assert result.exchange_order_id is not None
    assert adapter.place_order_call_count == 1


async def test_cb_normal_does_not_regress_pre_submit(pool: asyncpg.Pool) -> None:
    """negative — 정상 CB 레벨(기본값)에서는 이 신규 확인이 전혀 개입하지
    않고 기존 동작(ALLOW)이 유지된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    result = await submit_order(
        order,
        user_id=user_id,
        adapter=adapter,
        pool=pool,
        pre_submit_gate=gate,
    )

    assert result.exchange_order_id is not None
    assert adapter.place_order_call_count == 1


async def test_invalid_cb_level_rejected_by_db_constraint_failure_injection(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입 — 코드가 신뢰하는 `cb_level in ("halted", "emergency")` 집합
    밖의 값이 DB에 들어갈 수 있다면 그 fail-closed 비교 자체가 무력화된다.
    `system_safety_state.circuit_breaker_level`의 CHECK 제약이 5개 알려진
    레벨 밖의 값을 애초에 거부함을 직접 증명해, 코드의 집합 비교가 실제로
    방어 가능한 입력 도메인 위에서 동작함을 보인다."""
    with pytest.raises(asyncpg.CheckViolationError):
        await _set_cb_level(pool, "not_a_real_level")


@pytest.mark.perf
async def test_cb_halted_denial_latency_within_pre_submit_budget(pool: asyncpg.Pool) -> None:
    """성능 단언 — 신규 `read_safety_state()` 재확인 왕복이 추가돼도 PRE_SUBMIT
    게이트는 여전히 R-35 TTL(§3.3 `decision_ttl.pre_submit_sec` = 2.0s)보다
    훨씬 빠르게 DENY를 반환해야 한다(왕복 1회 추가는 수 ms 수준이어야 함)."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    await _set_cb_level(pool, "halted")
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    start = time.perf_counter()
    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order,
            user_id=user_id,
            adapter=adapter,
            pool=pool,
            pre_submit_gate=gate,
        )
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"CB halted DENY에 {elapsed:.3f}s — R-35 TTL(2.0s) 예산 위협"
