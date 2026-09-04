"""L4_risk_and_safety_v1.0.md#§9 R-45 — Circuit Breaker 재가동 루프 배선.

Spec: §2 표 139행, §4.3 CB 상태표 422~424행, §3.4 reactivation 정책, I5(§8 394행).
매 tick(운영 10s, `background_loops.py`) — 수집→`cb.evaluate()`(격상/완화·
대기요청·재악화취소는 R-43)→R-44 `can_reactivate()` 순수 판정→ALLOW일 때만
`cb.check_reactivation()`으로 전이 커밋. can_reactivate 자체는 재구현하지 않는다.

설계 노트(새 마이그레이션·컬럼 추측 없이 기존 스키마로 §4.3을 조립):
1. evidence_ref — `approval_requests` 전용 컬럼 없음(R-53 미착수). 자유 JSONB
   `context.evidence_ref`를 읽는다 — 채우는 경로가 없어 오늘은 항상 None.
2. metrics_history 단위 — recovery_gate.py는 "매 초 1개 표본" 전제지만 이
   모듈은 단일 10s 파이프라인이라(DoD⑥, 새 서브루프 금지) cooldown_sec(초)을
   tick 간격으로 나눠 "필요 tick 수"로 바꾸고 bounded deque로 유지한다.
3. fresh_risk_outcome — RECOVERY 실평가(R-53) 없어 수집 metrics를
   `compute_level()`로 재평가해 halted/emergency 아니면 ALLOW로 본다.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import asyncpg

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome
from src.core.safety import recovery_gate
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    CircuitBreakerService,
    compute_level,
)
from src.core.safety.data_freshness import DataFreshnessTracker
from src.core.safety.metrics_collector import ApiCallTracker, collect_circuit_breaker_metrics

TICK_INTERVAL_SECONDS = 10.0

_REACTIVATABLE = (CircuitBreakerLevel.HALTED, CircuitBreakerLevel.EMERGENCY)
_TERMINAL_NON_APPROVED = ("REJECTED", "EXPIRED", "CANCELLED")

MetricsHistory = deque[CircuitBreakerMetrics]


def cooldown_ticks(policy: RiskPolicy, *, tick_interval_sec: float = TICK_INTERVAL_SECONDS) -> int:
    """설계 노트 2 — history maxlen과 can_reactivate(cooldown_sec=...)가 항상
    같은 단위(tick 수)를 쓰도록 공유하는 단일 계산."""
    return max(1, round(policy.reactivation.cooldown_sec / tick_interval_sec))


async def run_circuit_breaker_tick(
    pool: asyncpg.Pool,
    cb: CircuitBreakerService,
    tracker: ApiCallTracker,
    freshness: DataFreshnessTracker | None,
    policy: RiskPolicy,
    *,
    history: MetricsHistory,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    metrics = await collect_circuit_breaker_metrics(pool, tracker, freshness)
    history.append(metrics)
    await cb.evaluate(metrics)
    await _check_reactivation(pool, cb, metrics, policy, history=history, now=now)


async def _check_reactivation(
    pool: asyncpg.Pool,
    cb: CircuitBreakerService,
    fresh_metrics: CircuitBreakerMetrics,
    policy: RiskPolicy,
    *,
    history: MetricsHistory,
    now: Callable[[], datetime],
) -> None:
    state = await cb.get_state()
    if state.reactivation_approval_id is None:
        return  # 대기 중 요청 없음 — 이번 tick은 할 일이 없다.
    request = await approval.get_request(pool, state.reactivation_approval_id)
    approval_status = _effective_status(
        request, ttl_sec=policy.reactivation.approval_ttl_sec, now=now()
    )
    fresh_level = compute_level(fresh_metrics, policy.circuit_breaker)
    fresh_outcome = RiskOutcome.DENY if fresh_level in _REACTIVATABLE else RiskOutcome.ALLOW
    decision = recovery_gate.can_reactivate(
        current_level=state.level,
        metrics_history=tuple(history),
        cooldown_sec=cooldown_ticks(policy),
        evidence_ref=request.context.get("evidence_ref"),
        approval_status=approval_status,
        fresh_risk_outcome=fresh_outcome,
    )
    if decision.outcome == RiskOutcome.ALLOW:
        await cb.check_reactivation()
        await _deactivate_cb_provider_controls(pool)
    elif request.status in _TERMINAL_NON_APPROVED:
        # DB 원본 status로만 판단(TTL 파생 approval_status를 쓰면 DB가 여전히
        # APPROVED라 check_reactivation()이 그대로 normal 전이해 TTL이 무력화됨).
        await cb.check_reactivation()
    # else: PENDING이거나 다른 조건 미달(TTL 초과 포함) — 대기(fail-closed, I5).


def _effective_status(request: approval.ApprovalRequest, *, ttl_sec: int, now: datetime) -> str:
    """§3.4 approval_ttl_sec — APPROVED라도 resolved_at 기준 ttl_sec 초과면
    "APPROVED 아님"으로 보고한다(recovery_gate는 시계를 보지 않는다)."""
    if request.status == "APPROVED" and request.resolved_at is not None:
        if now - request.resolved_at > timedelta(seconds=ttl_sec):
            return "EXPIRED"
    return request.status


async def _deactivate_cb_provider_controls(pool: asyncpg.Pool) -> None:
    """§4.3 423행 "cb:* PROVIDER control INACTIVE(재개는 아님)" — restricted
    격상 시 만드는 배선(420행)은 별도 리프라 오늘은 행이 없을 수도 있다."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
            "WHERE scope = 'PROVIDER' AND state = 'ACTIVE' AND reason LIKE 'cb:%'"
        )
