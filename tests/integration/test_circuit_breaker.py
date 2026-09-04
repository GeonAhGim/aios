"""9.4/9.4b 통합테스트 — 실제 dev DB의 system_safety_state(단일 행) 대상.

각 테스트 시작 전 normal/재가동없음으로 리셋해 전역 싱글톤 행 상태를
격리한다.
"""
import asyncio
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.approval import service as approval
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    CircuitBreakerService,
    compute_level,
)
from src.core.safety.data_freshness import DataFreshnessTracker
from src.core.safety.metrics_collector import ApiCallTracker, collect_circuit_breaker_metrics

_LEVEL_SEVERITY = {
    CircuitBreakerLevel.NORMAL: 0,
    CircuitBreakerLevel.WARNING: 1,
    CircuitBreakerLevel.RESTRICTED: 2,
    CircuitBreakerLevel.HALTED: 3,
    CircuitBreakerLevel.EMERGENCY: 4,
}


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def policy():
    return load_risk_policy().circuit_breaker


def test_compute_level_picks_most_severe_indicator(policy):
    metrics = CircuitBreakerMetrics(
        api_error_rate_pct=Decimal("12"),  # warning 임계(10) 초과 -> warning
        data_delay_sec=Decimal("6"),  # halted 임계(5) 초과 -> halted
    )
    assert compute_level(metrics, policy) == CircuitBreakerLevel.HALTED


def test_compute_level_normal_when_nothing_triggered(policy):
    assert compute_level(CircuitBreakerMetrics(), policy) == CircuitBreakerLevel.NORMAL


async def test_warning_auto_upgrades_and_downgrades(pool, policy):
    service = CircuitBreakerService(pool, policy)

    up = await service.evaluate(CircuitBreakerMetrics(api_error_rate_pct=Decimal("12")))
    assert up.level == CircuitBreakerLevel.WARNING

    down = await service.evaluate(CircuitBreakerMetrics())
    assert down.level == CircuitBreakerLevel.NORMAL  # warning은 자동 하향 허용


async def test_halted_does_not_auto_downgrade_and_creates_reactivation_request(pool, policy):
    service = CircuitBreakerService(pool, policy)

    halted = await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))
    assert halted.level == CircuitBreakerLevel.HALTED

    recovered_metrics = CircuitBreakerMetrics()
    result = await service.evaluate(recovered_metrics)

    assert result.level == CircuitBreakerLevel.HALTED  # 자동 하향 안 됨
    assert result.reactivation_approval_id is not None

    request = await approval.get_request(pool, result.reactivation_approval_id)
    assert request.scope == "PLATFORM"
    assert request.mandatory_wait_seconds == 180


async def test_reactivation_approval_completes_transition_to_normal(pool, policy):
    service = CircuitBreakerService(pool, policy)
    await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))
    state = await service.evaluate(CircuitBreakerMetrics())
    request_id = state.reactivation_approval_id

    # 실제 180초 대기 없이 승인 가능 상태로 만든다(대기시간 경과 시뮬레이션).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = now() - interval '181 seconds' "
            "WHERE id = $1",
            request_id,
        )
    from uuid import uuid4

    await approval.approve(pool, request_id, uuid4())

    final_state = await service.check_reactivation()
    assert final_state.level == CircuitBreakerLevel.NORMAL
    assert final_state.reactivation_approval_id is None


async def test_worsening_during_reactivation_wait_cancels_request(pool, policy):
    service = CircuitBreakerService(pool, policy)
    await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))
    pending = await service.evaluate(CircuitBreakerMetrics())
    request_id = pending.reactivation_approval_id
    assert request_id is not None

    # 대기 중 재악화 — 다시 halted를 유발하는 지표
    worsened = await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))

    assert worsened.level == CircuitBreakerLevel.HALTED
    assert worsened.reactivation_approval_id is None
    cancelled_request = await approval.get_request(pool, request_id)
    assert cancelled_request.status == "CANCELLED"


async def test_wiring_high_tracker_error_rate_escalates_via_collected_metrics(pool, policy):
    """main.py::_safety_reactivation_loop 실배선 검증 — ApiCallTracker에
    쌓인 실패율이 collect_circuit_breaker_metrics()를 거쳐 evaluate()로
    전달되면 실제로 격상되는지, 두 leaf(tracker/collector)가 아니라
    이어붙인 전체 경로로 확인한다. api_error_rate_pct=30%는 restricted
    임계(25%) 이상이라 order_reject_rate_pct/daily_loss_pct가 공유
    dev/test DB의 다른 데이터로 얼마가 나오든(둘 다 그 자체로는 severity를
    낮추지 못함 — compute_level은 최댓값 채택) 최소 RESTRICTED 이상이
    보장된다."""
    service = CircuitBreakerService(pool, policy)
    tracker = ApiCallTracker()
    for _ in range(7):
        tracker.record_success()
    for _ in range(3):
        tracker.record_failure()

    metrics = await collect_circuit_breaker_metrics(pool, tracker)
    result = await service.evaluate(metrics)

    assert _LEVEL_SEVERITY[result.level] >= _LEVEL_SEVERITY[CircuitBreakerLevel.RESTRICTED]


async def test_escalation_from_halted_to_emergency_allowed_even_while_pending(pool, policy):
    service = CircuitBreakerService(pool, policy)
    await service.evaluate(CircuitBreakerMetrics(data_delay_sec=Decimal("6")))  # halted

    emergency = await service.evaluate(
        CircuitBreakerMetrics(daily_loss_pct=Decimal("6"))  # emergency 임계(5) 초과
    )
    assert emergency.level == CircuitBreakerLevel.EMERGENCY


async def test_unknown_data_delay_does_not_read_as_normal(pool, policy):
    """R-43 negative — freshness_tracker에 관측이 0건이면
    collect_circuit_breaker_metrics()는 data_delay_sec=None("모름")을 돌려준다.
    None을 "지연 없음"으로 읽으면 CB가 stale 데이터에서도 절대 트립하지 않는
    fail-open이 된다(§9 R-43 원문 결함) — evaluate()가 NORMAL을 내면 안 된다."""
    service = CircuitBreakerService(pool, policy)
    metrics = await collect_circuit_breaker_metrics(
        pool, ApiCallTracker(), DataFreshnessTracker()
    )
    assert metrics.data_delay_sec is None

    result = await service.evaluate(metrics)

    assert result.level != CircuitBreakerLevel.NORMAL
    assert _LEVEL_SEVERITY[result.level] >= _LEVEL_SEVERITY[CircuitBreakerLevel.HALTED]


async def test_concurrent_set_level_only_one_writer_wins(pool, policy):
    """105번 §4.1 형태 A — 같은 stale `expected`(둘 다 NORMAL을 읽었다고 가정)에서
    두 코루틴이 서로 다른 레벨로 동시에 `_set_level` CAS UPDATE를 시도하면,
    Postgres 행 잠금이 둘을 직렬화한다 — 먼저 커밋한 쪽만 성공하고, 나중 쪽은
    조용히 덮어쓰지 못하고 ConcurrencyConflictError를 받는다(105 위반이면
    `WHERE` 조건이 없어 둘 다 성공 - 조용한 lost update)."""
    service = CircuitBreakerService(pool, policy)
    stale = await service.get_state()
    assert stale.level == CircuitBreakerLevel.NORMAL

    results = await asyncio.gather(
        service._set_level(
            CircuitBreakerLevel.WARNING, reactivation_approval_id=None, expected=stale
        ),
        service._set_level(
            CircuitBreakerLevel.RESTRICTED, reactivation_approval_id=None, expected=stale
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1

    final = await service.get_state()
    assert final.level in (CircuitBreakerLevel.WARNING, CircuitBreakerLevel.RESTRICTED)
