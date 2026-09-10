"""R-45 통합테스트 — services/safety/circuit_breaker_loop.py.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-45, §4.3 CB 상태표 422~424행,
I5(§8 394행). task-1363 decision — 실시간 타이밍은 주입 clock으로만 검증하고
asyncio.sleep 대기 단언은 쓰지 않는다(test_split_brain/test_base_loop 결정론화
선례). `_check_reactivation`은 이 모듈이 직접 참조하는 사적 헬퍼다(기존
`test_circuit_breaker.py`도 `service._set_level`을 직접 호출하는 동일 관례).

DEEPEN(task-2832, DEPTH 감사 docs/audit/DEPTH_R_EO.md#1363) D2->D3 증빙:
4가지 거부사유·재악화취소는 탄탄했으나 성능단언·실패주입·게이트 적색
재현(실제 배선 진입점 경유)·다중 인스턴스 동시성 증거가 없었다(안전축 R은
D3 하한). 아래에 추가한다: (1) 지표 수집 실패가 삼켜지지 않고 그대로
전파되는지(I2 fail-closed, 실패주입), (2) 수집기가 실제로 임계 초과 지표를
반환할 때 `run_circuit_breaker_tick`(공개 진입점)만으로 halted(적색)가
재현되는지(수동 `cb.evaluate()` 호출이 아닌 배선 경유, I-10), (3) 큰
metrics_history를 반복 스캔해도 예산 내에 끝나는지(성능 단언), (4) 두
엔진 인스턴스가 같은 승인된 재가동 요청에 동시에 `_check_reactivation`을
쳐도 CAS(105번 §4.2 형태 B)가 정확히 한 번만 전이시키고 나머지는 조용히
성공한 척 흘리지 않는지(다중 인스턴스 동시성, D3).
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.core.approval import service as approval
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    CircuitBreakerService,
)
from src.core.safety.data_freshness import DataFreshnessTracker
from src.core.safety.metrics_collector import ApiCallTracker
from src.services.safety.circuit_breaker_loop import (
    _check_reactivation,
    cooldown_ticks,
    run_circuit_breaker_tick,
)
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


def _fresh_freshness_tracker() -> DataFreshnessTracker:
    """`freshness=None`이면 `collect_circuit_breaker_metrics`가 data_delay_sec을
    항상 None("모름")으로 채우고, compute_level은 그걸 항상 halted 임계
    초과로 fail-closed 처리한다(metrics_collector.py 설계 노트) — 그러면
    run_circuit_breaker_tick 자체를 도는 이 테스트들은 지표와 무관하게
    항상 halted를 관측하게 돼 무의미해진다. 실측을 흉내내 최근 close_time을
    기록해둔다."""
    tracker = DataFreshnessTracker()
    tracker.record("binance", "BTC/USDT", datetime.now(timezone.utc))
    return tracker


async def test_run_circuit_breaker_tick_collects_and_evaluates_via_public_entrypoint(
    pool, cb, policy
):
    """`_check_reactivation`만 테스트하면 배선 진입점인
    `run_circuit_breaker_tick`(수집→evaluate→recovery_gate→check_reactivation
    전체)이 실제로 조립돼 있다는 증거가 안 된다(I-10) — 여기서는 그 공개
    함수를 직접(스케줄러 없이) 호출해 수집→evaluate가 실행되고 history에
    표본이 쌓이는지 본다."""
    history: deque[CircuitBreakerMetrics] = deque(maxlen=cooldown_ticks(policy))

    await run_circuit_breaker_tick(
        pool, cb, ApiCallTracker(), _fresh_freshness_tracker(), policy, history=history
    )

    assert len(history) == 1
    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.NORMAL  # 빈 테스트 DB + 실측 지표 -> 정상


async def test_run_circuit_breaker_tick_drives_full_reactivation_end_to_end(
    pool, cb, policy, events
):
    """같은 진입점으로 halted -> (승인+evidence+cooldown) -> normal 전체
    파이프라인이 한 번의 tick 호출로 조립돼 동작하는지 증명한다."""
    request_id = await _make_halted_with_pending_request(pool, cb)
    await _approve(pool, request_id, evidence_ref="ev-1")

    history = _full_history(policy)
    later = lambda: datetime.now(timezone.utc)  # noqa: E731

    await run_circuit_breaker_tick(
        pool,
        cb,
        ApiCallTracker(),
        _fresh_freshness_tracker(),
        policy,
        history=history,
        now=later,
    )

    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.NORMAL
    assert state.reactivation_approval_id is None
    assert events.count("risk.circuit_breaker.reactivated") == 1


async def test_run_circuit_breaker_tick_propagates_collector_failure_not_swallowed(
    pool, cb, policy, monkeypatch
):
    """실패 주입(I2 fail-closed) — 지표 수집이 DB 레벨에서 실패하면 예외가
    그대로 전파돼야 한다. 조용히 삼키고 evaluate를 건너뛰면 CB가 다음
    tick까지 낡은 상태로 남아 fail-open 위험(§9 R-43 재발)이 생긴다."""

    async def _failing_collector(pool, tracker, freshness):
        raise asyncpg.PostgresConnectionError("simulated metrics collection failure")

    monkeypatch.setattr(
        "src.services.safety.circuit_breaker_loop.collect_circuit_breaker_metrics",
        _failing_collector,
    )

    history: deque[CircuitBreakerMetrics] = deque(maxlen=cooldown_ticks(policy))
    with pytest.raises(asyncpg.PostgresConnectionError):
        await run_circuit_breaker_tick(
            pool, cb, ApiCallTracker(), _fresh_freshness_tracker(), policy, history=history
        )

    assert len(history) == 0  # 실패한 표본은 이력에 남지 않는다
    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.NORMAL  # 실패 전 상태 그대로


async def test_run_circuit_breaker_tick_reproduces_gate_red_via_collected_metrics(
    pool, cb, policy, events, monkeypatch
):
    """게이트 적색 재현(I-10 배선 증거) — 기존 halted 테스트들은
    `cb.evaluate()`를 직접 호출해 halted를 수동으로 만든다. 여기서는 실제
    공개 진입점인 `run_circuit_breaker_tick`이 (위조한) 수집기가 반환한
    임계 초과 지표를 통해 실제로 evaluate를 거쳐 halted(적색)로 전이시키는지
    — 수동 호출이 아니라 배선된 파이프라인 자체가 조립돼 동작하는지 —
    증명한다."""

    async def _bad_collector(pool, tracker, freshness):
        return _BAD_METRICS

    monkeypatch.setattr(
        "src.services.safety.circuit_breaker_loop.collect_circuit_breaker_metrics",
        _bad_collector,
    )

    history: deque[CircuitBreakerMetrics] = deque(maxlen=cooldown_ticks(policy))
    await run_circuit_breaker_tick(
        pool, cb, ApiCallTracker(), _fresh_freshness_tracker(), policy, history=history
    )

    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.HALTED
    assert len(history) == 1
    assert events.count("risk.circuit_breaker.level_changed") == 1


async def test_check_reactivation_meets_latency_budget_with_large_history(pool, cb, policy):
    """성능 단언(D2) — `can_reactivate`의 baseline 스캔은 metrics_history
    길이에 비례한다(O(n)). 큰 이력(5000 표본)을 매 호출마다 반복 스캔해도
    DB 왕복을 합쳐 예산 안에 끝나는지 확인한다 — 스캔 비용이 퇴화하면
    (예: O(n^2)) 이 예산을 넘는다. fresh_risk_outcome을 매번 DENY로 고정해
    (evidence+cooldown은 통과, 마지막 조건만 거부) 매 반복이 전체 이력을
    끝까지 스캔하도록 강제한다."""
    request_id = await _make_halted_with_pending_request(pool, cb)
    await _approve(pool, request_id, evidence_ref="ev-1")

    n = 5000
    history: deque[CircuitBreakerMetrics] = deque([CircuitBreakerMetrics()] * n, maxlen=n)
    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    iterations = 10
    budget_sec = 5.0

    start = time.perf_counter()
    for _ in range(iterations):
        await _check_reactivation(pool, cb, _BAD_METRICS, policy, history=history, now=now)
    elapsed = time.perf_counter() - start

    state = await cb.get_state()
    assert state.level == CircuitBreakerLevel.HALTED  # fresh_deny가 매번 거부 -> 전이 없음
    assert elapsed < budget_sec, (
        f"_check_reactivation {iterations}x(history={n}) 가 예산({budget_sec}s)을 "
        f"넘었습니다({elapsed:.3f}s) — 이력 스캔 비용 회귀 확인 필요."
    )


async def test_concurrent_reactivation_checks_only_one_instance_wins_the_transition(
    pool, policy, events
):
    """다중 인스턴스 동시성(D3) — 여러 엔진 프로세스가 동시에 같은 10s tick을
    돌려 `_check_reactivation`을 동시에 호출하는 상황을 시뮬레이션한다.
    `CircuitBreakerService._set_level`의 CAS(105번 §4.2 형태 B)가 두 호출
    중 정확히 하나만 통과시키고, 나머지는 조용히 성공한 것처럼 흘리지
    않고 `ConcurrencyConflictError`로 명시 거부하거나(먼저 읽은 스냅샷이
    이미 낡음) 최신 상태를 다시 읽어 조용히 할 일 없음으로 끝난다(나중에
    읽은 쪽) — 어느 쪽이든 재가동 이벤트는 정확히 한 번만 발행된다."""

    async def _publish(event_type, payload):
        events.append(event_type)

    cb_a = CircuitBreakerService(pool, policy.circuit_breaker, publish=_publish)
    cb_b = CircuitBreakerService(pool, policy.circuit_breaker, publish=_publish)

    request_id = await _make_halted_with_pending_request(pool, cb_a)
    await _approve(pool, request_id, evidence_ref="ev-1")

    history = _full_history(policy)
    now = lambda: datetime.now(timezone.utc)  # noqa: E731

    results = await asyncio.gather(
        _check_reactivation(pool, cb_a, CircuitBreakerMetrics(), policy, history=history, now=now),
        _check_reactivation(pool, cb_b, CircuitBreakerMetrics(), policy, history=history, now=now),
        return_exceptions=True,
    )

    assert len(results) == 2
    for result in results:
        if result is not None:
            assert isinstance(result, ConcurrencyConflictError), result

    state = await cb_a.get_state()
    assert state.level == CircuitBreakerLevel.NORMAL
    assert state.reactivation_approval_id is None
    assert events.count("risk.circuit_breaker.reactivated") == 1  # 정확히 한 번만 전이·발행
