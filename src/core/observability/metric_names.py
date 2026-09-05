"""메트릭 이름 단일 출처.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-04.
값은 §7.2 표 원문 리터럴이며, 계측 지점(PLT-10 이후)은 이 모듈의 상수만 참조한다
(문자열 리터럴을 직접 쓰지 않는다 — 오타·중복 정의를 정적으로 막기 위함).

Prometheus 노출 시 이름의 `.`는 `_`로 치환한다(`to_prom`) — Prometheus 메트릭 이름은
`.`를 허용하지 않는다.

미검증: `aios.readiness.status`는 §7.2 표 원문이지만 §6 단위테스트 표의 정규식
(`aios.<context>.<subject>.<verb>` 4-세그먼트)과 형식이 맞지 않는다. 이 리프의 DoD가
"정규식 전수 통과"를 요구하므로 `aios.readiness.check.status`로 세그먼트를 추가했다 —
PLT-11(alert_rules.yaml)이 이 상수 값을 그대로 참조하면 문서 표기와의 괴리는 생기지 않는다.
"""
from __future__ import annotations

API_REQUEST_COUNT_TOTAL = "aios.api.request.count_total"
API_REQUEST_DURATION_SECONDS = "aios.api.request.duration_seconds"

ORDER_SUBMIT_COUNT_TOTAL = "aios.order.submit.count_total"
ORDER_SUBMIT_DURATION_SECONDS = "aios.order.submit.duration_seconds"
ORDER_FILL_COUNT_TOTAL = "aios.order.fill.count_total"
ORDER_UNKNOWN_STATE_GAUGE = "aios.order.unknown_state.gauge"

RISK_DECISION_COUNT_TOTAL = "aios.risk.decision.count_total"
RISK_EVALUATION_DURATION_SECONDS = "aios.risk.evaluation.duration_seconds"

# L4 risk_and_safety §7 "post-fence 부작용 — 목표 0 — fenced_submit" 행 원문.
SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL = "aios.safety.post_fence_side_effect.count_total"

FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL = (
    "aios.foundation_paper_control.order_intent.count_total"
)

LOOP_TICK_COUNT_TOTAL = "aios.loop.tick.count_total"
LOOP_TICK_DURATION_SECONDS = "aios.loop.tick.duration_seconds"
LOOP_LAST_SUCCESS_AGE_SECONDS = "aios.loop.last_success_age.seconds"

ADAPTER_REQUEST_COUNT_TOTAL = "aios.adapter.request.count_total"
ADAPTER_REQUEST_DURATION_SECONDS = "aios.adapter.request.duration_seconds"

EVENT_BUS_QUEUE_DEPTH_GAUGE = "aios.event_bus.queue_depth.gauge"
EVENT_BUS_HANDLER_COUNT_TOTAL = "aios.event_bus.handler.count_total"

AUTH_LOGIN_COUNT_TOTAL = "aios.auth.login.count_total"
AUTH_LOCKOUT_COUNT_TOTAL = "aios.auth.lockout.count_total"
AUTH_REFRESH_REUSE_COUNT_TOTAL = "aios.auth.refresh_reuse.count_total"
AUTH_TENANT_MISMATCH_COUNT_TOTAL = "aios.auth.tenant_mismatch.count_total"
AUTH_RATE_LIMITED_COUNT_TOTAL = "aios.auth.rate_limited.count_total"

AUDIT_APPEND_COUNT_TOTAL = "aios.audit.append.count_total"

SECURITY_SECRET_DECRYPT_COUNT_TOTAL = "aios.security.secret_decrypt.count_total"
SECURITY_BREAK_GLASS_COUNT_TOTAL = "aios.security.break_glass.count_total"
SECURITY_KEY_ROTATION_COUNT_TOTAL = "aios.security.key_rotation.count_total"

READINESS_CHECK_STATUS = "aios.readiness.check.status"

POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL = (
    "aios.positions.reconciliation.mismatch_count_total"
)

POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL = (
    "aios.positions.scheduler.cycle_failure_count_total"
)
POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE = "aios.positions.scheduler_cycle_success.gauge"

ALL_METRIC_NAMES: frozenset[str] = frozenset(
    {
        API_REQUEST_COUNT_TOTAL,
        API_REQUEST_DURATION_SECONDS,
        ORDER_SUBMIT_COUNT_TOTAL,
        ORDER_SUBMIT_DURATION_SECONDS,
        ORDER_FILL_COUNT_TOTAL,
        ORDER_UNKNOWN_STATE_GAUGE,
        RISK_DECISION_COUNT_TOTAL,
        RISK_EVALUATION_DURATION_SECONDS,
        SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL,
        FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL,
        LOOP_TICK_COUNT_TOTAL,
        LOOP_TICK_DURATION_SECONDS,
        LOOP_LAST_SUCCESS_AGE_SECONDS,
        ADAPTER_REQUEST_COUNT_TOTAL,
        ADAPTER_REQUEST_DURATION_SECONDS,
        EVENT_BUS_QUEUE_DEPTH_GAUGE,
        EVENT_BUS_HANDLER_COUNT_TOTAL,
        AUTH_LOGIN_COUNT_TOTAL,
        AUTH_LOCKOUT_COUNT_TOTAL,
        AUTH_REFRESH_REUSE_COUNT_TOTAL,
        AUTH_TENANT_MISMATCH_COUNT_TOTAL,
        AUTH_RATE_LIMITED_COUNT_TOTAL,
        AUDIT_APPEND_COUNT_TOTAL,
        SECURITY_SECRET_DECRYPT_COUNT_TOTAL,
        SECURITY_BREAK_GLASS_COUNT_TOTAL,
        SECURITY_KEY_ROTATION_COUNT_TOTAL,
        READINESS_CHECK_STATUS,
        POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL,
        POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL,
        POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE,
    }
)


def to_prom(name: str) -> str:
    """Prometheus 메트릭 이름 형식으로 변환한다(`.` -> `_`)."""
    return name.replace(".", "_")
