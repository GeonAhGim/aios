"""R-45 통합테스트 — services/safety/circuit_breaker_loop.py.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-45, §4.3 CB 상태표 422~424행,
I5(§8 394행). task-1363 decision — 실시간 타이밍은 주입 clock으로만 검증하고
asyncio.sleep 대기 단언은 쓰지 않는다(test_split_brain/test_base_loop 결정론화
선례). `_check_reactivation`은 이 모듈이 직접 참조하는 사적 헬퍼다(기존
`test_circuit_breaker.py`도 `service._set_level`을 직접 호출하는 동일 관례).
"""
from __future__ import annotations

import os
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    CircuitBreakerService,
)
from src.services.safety.circuit_breaker_loop import _check_reactivation, cooldown_ticks
from tests.integration.conftest import create_test_user

_BAD_METRICS = CircuitBreakerMetrics(data_delay_sec=Decimal("6"))  # halted 임계(5) 초과


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def policy():
    # cooldown_sec=20 -> cooldown_ticks()=2(기본 TICK_INTERVAL_SECONDS=10)로
    # 축소해 history 2개짜리로도 결정론적으로 재현한다(실시간 대기 없음).
    base = load_risk_policy()
    return base.model_copy(
        update={"reactivation": base.reactivation.model_copy(update={"cooldown_sec": 20})}
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def cb(pool, policy, events):
    async def _publish(event_type, payload):
        events.append(event_type)

    return CircuitBreakerService(pool, policy.circuit_breaker, publish=_publish)


async def _make_halted_with_pending_request(pool, cb) -> int:
    await cb.evaluate(_BAD_METRICS)
    state = await cb.evaluate(CircuitBreakerMetrics())  # 회복 -> 재가동 요청 생성
    assert state.level == CircuitBreakerLevel.HALTED
    assert state.reactivation_approval_id is not None
    return state.reactivation_approval_id


async def _approve(pool, request_id: int, *, evidence_ref: str | None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = now() - interval '181 seconds' "
            "WHERE id = $1",
            request_id,
        )
        if evidence_ref is not None:
            await conn.execute(
                "UPDATE approval_requests SET context = context || $2::jsonb WHERE id = $1",
                request_id,
                f'{{"evidence_ref": "{evidence_ref}"}}',
            )
    await approval.approve(pool, request_id, uuid4())


def _full_history(policy, *, metrics=None):
    n = cooldown_ticks(policy)
    return deque([metrics or CircuitBreakerMetrics()] * n, maxlen=n)


async def test_halted_never_auto_downgrades_only_creates_request(pool, cb, policy, events):
    """I5 — 지표가 정상으로 돌아오고 cooldown을 넘겨도(fake clock) 승인·evidence
    없이는 halted가 그대로다. 만들어지는 것은 재가동 요청뿐."""
    request_id = await _make_halted_with_pending_request(pool, cb)

    far_future = lambda: datetime.now(timezone.utc) + timedelta(hours=1)  # noqa: E731
    await _check_reactivation(
        pool, cb, CircuitBreakerMetrics(), policy, history=_full_history(policy), now=far_future
    )

    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.HALTED
    assert state.reactivation_approval_id == request_id  # PENDING이라 그대로 대기
    assert events.count("risk.circuit_breaker.reactivation_requested") == 1
    assert "risk.circuit_breaker.reactivated" not in events


async def test_approved_evidence_cooldown_transitions_to_normal_and_deactivates_cb_controls(
    pool, cb, policy, events
):
    """승인+evidence+cooldown(전부 baseline 이력) 충족 시에만 normal 전이 +
    cb:* PROVIDER control INACTIVE. 다른 scope의 control(=정지된 실행 시뮬레이션)은
    건드리지 않는다 — "재개는 아님"을 증명."""
    request_id = await _make_halted_with_pending_request(pool, cb)
    await _approve(pool, request_id, evidence_ref="ev-1")

    actor_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        cb_control_id = await conn.fetchval(
            "INSERT INTO safety_control (scope, scope_ref, reason, actor_subject_id, "
            "fence_token) VALUES ('PROVIDER', 'bitget', 'cb:halted', $1, 1) RETURNING id",
            actor_id,
        )
        unrelated_control_id = await conn.fetchval(
            "INSERT INTO safety_control (scope, scope_ref, reason, actor_subject_id, "
            "fence_token) VALUES ('ACCOUNT', $2, 'manual kill switch', $1, 1) RETURNING id",
            actor_id,
            str(actor_id),
        )

    await _check_reactivation(
        pool,
        cb,
        CircuitBreakerMetrics(),
        policy,
        history=_full_history(policy),
        now=lambda: datetime.now(timezone.utc),
    )

    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.NORMAL
    assert state.reactivation_approval_id is None
    assert events.count("risk.circuit_breaker.reactivated") == 1

    async with pool.acquire() as conn:
        cb_row = await conn.fetchrow(
            "SELECT state FROM safety_control WHERE id = $1", cb_control_id
        )
        unrelated_row = await conn.fetchrow(
            "SELECT state FROM safety_control WHERE id = $1", unrelated_control_id
        )
    assert cb_row["state"] == "INACTIVE"
    assert unrelated_row["state"] == "ACTIVE"  # 실행/주문 pause는 이 경로가 재개하지 않는다


@pytest.mark.parametrize(
    "case",
    ["missing_evidence", "cooldown_not_met", "approval_expired", "fresh_deny"],
)
async def test_four_rejections_delegate_to_can_reactivate_and_block_normal(
    pool, cb, policy, case
):
    request_id = await _make_halted_with_pending_request(pool, cb)
    evidence_ref = None if case == "missing_evidence" else "ev-1"
    await _approve(pool, request_id, evidence_ref=evidence_ref)

    history = deque([CircuitBreakerMetrics()], maxlen=cooldown_ticks(policy))  # 1개뿐 -> 미달
    if case != "cooldown_not_met":
        history = _full_history(policy)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    if case == "approval_expired":
        now = lambda: datetime.now(timezone.utc) + timedelta(seconds=10_000)  # noqa: E731

    fresh = _BAD_METRICS if case == "fresh_deny" else CircuitBreakerMetrics()

    await _check_reactivation(pool, cb, fresh, policy, history=history, now=now)

    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.HALTED  # 4가지 중 무엇도 normal을 만들지 않는다


async def test_worsening_while_pending_cancels_request(pool, cb, events):
    request_id = await _make_halted_with_pending_request(pool, cb)

    worsened = await cb.evaluate(_BAD_METRICS)  # 대기 중 재악화

    assert worsened.level == CircuitBreakerLevel.HALTED
    assert worsened.reactivation_approval_id is None
    cancelled = await approval.get_request(pool, request_id)
    assert cancelled.status == "CANCELLED"
    assert events.count("risk.circuit_breaker.reactivation_cancelled") == 1
