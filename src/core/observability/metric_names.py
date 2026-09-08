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

# L4_risk_and_safety §7 verbatim row "replay mismatch — target 0 — replay" (R-54).
CORE_RISK_REPLAY_MISMATCH_COUNT_TOTAL = "aios.core_risk.replay_mismatch.count_total"

# DC-17 — application/realtime_fanout.py instrumentation points.
MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL = "aios.market_data.fanout_publish.count_total"
MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL = "aios.market_data.fanout_deny.count_total"
MARKET_DATA_FANOUT_DROPPED_COUNT_TOTAL = "aios.market_data.fanout_drop.count_total"

# L4_execution_oms_and_exchange_v1.0.md §7.2 (§9 L4-27) — oms/exchange/paper_sim.
OMS_ORDER_SUBMIT_COUNT_TOTAL = "aios.oms.order_submit.count_total"
OMS_ORDER_SUBMIT_DURATION_SECONDS = "aios.oms.order_submit.duration_seconds"
OMS_ORDER_TRANSITION_COUNT_TOTAL = "aios.oms.order_transition.count_total"
OMS_OUTBOX_BACKLOG_GAUGE = "aios.oms.outbox.backlog"

# Original (§7.2): `aios.oms.outbox.dispatch.duration_seconds` — 4 segments, so
# it violates the §6 regex. Normalized by merging `outbox`+`dispatch` into one
# segment (same precedent as PLT-04).
OMS_OUTBOX_DISPATCH_DURATION_SECONDS = "aios.oms.outbox_dispatch.duration_seconds"

# Original: `aios.oms.inbox.duplicate.count_total` — 4 segments. Merged `inbox`+`duplicate`.
OMS_INBOX_DUPLICATE_COUNT_TOTAL = "aios.oms.inbox_duplicate.count_total"

OMS_INBOX_LAG_SECONDS_GAUGE = "aios.oms.inbox.lag_seconds"

# Original: `aios.oms.unknown_orders` — only 2 segments, so a `.gauge` segment
# is appended (same pattern as the EVENT_BUS_QUEUE_DEPTH_GAUGE precedent).
OMS_UNKNOWN_ORDERS_GAUGE = "aios.oms.unknown_orders.gauge"

OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS = "aios.oms.unknown_resolution.duration_seconds"

# Original: `aios.oms.reconcile.run.count_total` — 4 segments. Merged `reconcile`+`run`.
OMS_RECONCILE_RUN_COUNT_TOTAL = "aios.oms.reconcile_run.count_total"

OMS_RECONCILE_LAG_SINCE_HEALTHY_SECONDS_GAUGE = "aios.oms.reconcile.lag_since_healthy_seconds"

# Original: `aios.oms.algo.slice_submit.count_total` — 4 segments. Merged `algo`+`slice_submit`.
OMS_ALGO_SLICE_SUBMIT_COUNT_TOTAL = "aios.oms.algo_slice_submit.count_total"

EXCHANGE_HTTP_REQUEST_COUNT_TOTAL = "aios.exchange.http_request.count_total"
EXCHANGE_HTTP_REQUEST_DURATION_SECONDS = "aios.exchange.http_request.duration_seconds"
EXCHANGE_RATE_LIMIT_WAIT_SECONDS = "aios.exchange.rate_limit.wait_seconds"
EXCHANGE_CIRCUIT_STATE_GAUGE = "aios.exchange.circuit.state"

# Original: `aios.exchange.clock_offset_ms` — only 2 segments, so a `.gauge` segment is appended.
EXCHANGE_CLOCK_OFFSET_MS_GAUGE = "aios.exchange.clock_offset_ms.gauge"

# Original: `aios.exchange.ws.reconnect.count_total` — 4 segments. Merged `ws`+`reconnect`.
EXCHANGE_WS_RECONNECT_COUNT_TOTAL = "aios.exchange.ws_reconnect.count_total"
# Original: `aios.exchange.ws.sequence_gap.count_total` — 4 segments. Merged `ws`+`sequence_gap`.
EXCHANGE_WS_SEQUENCE_GAP_COUNT_TOTAL = "aios.exchange.ws_sequence_gap.count_total"
# Original: `aios.exchange.ws.heartbeat_miss.count_total` — 4 segments. Merged `ws`+
# `heartbeat_miss`.
EXCHANGE_WS_HEARTBEAT_MISS_COUNT_TOTAL = "aios.exchange.ws_heartbeat_miss.count_total"

PAPER_SIM_FILL_COUNT_TOTAL = "aios.paper_sim.fill.count_total"
# Original: `aios.paper_sim.slippage_bps` — only 2 segments, so split into `slippage`/`bps`.
PAPER_SIM_SLIPPAGE_BPS = "aios.paper_sim.slippage.bps"

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
        CORE_RISK_REPLAY_MISMATCH_COUNT_TOTAL,
        MARKET_DATA_FANOUT_PUBLISHED_COUNT_TOTAL,
        MARKET_DATA_FANOUT_DENIED_COUNT_TOTAL,
        MARKET_DATA_FANOUT_DROPPED_COUNT_TOTAL,
        OMS_ORDER_SUBMIT_COUNT_TOTAL,
        OMS_ORDER_SUBMIT_DURATION_SECONDS,
        OMS_ORDER_TRANSITION_COUNT_TOTAL,
        OMS_OUTBOX_BACKLOG_GAUGE,
        OMS_OUTBOX_DISPATCH_DURATION_SECONDS,
        OMS_INBOX_DUPLICATE_COUNT_TOTAL,
        OMS_INBOX_LAG_SECONDS_GAUGE,
        OMS_UNKNOWN_ORDERS_GAUGE,
        OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS,
        OMS_RECONCILE_RUN_COUNT_TOTAL,
        OMS_RECONCILE_LAG_SINCE_HEALTHY_SECONDS_GAUGE,
        OMS_ALGO_SLICE_SUBMIT_COUNT_TOTAL,
        EXCHANGE_HTTP_REQUEST_COUNT_TOTAL,
        EXCHANGE_HTTP_REQUEST_DURATION_SECONDS,
        EXCHANGE_RATE_LIMIT_WAIT_SECONDS,
        EXCHANGE_CIRCUIT_STATE_GAUGE,
        EXCHANGE_CLOCK_OFFSET_MS_GAUGE,
        EXCHANGE_WS_RECONNECT_COUNT_TOTAL,
        EXCHANGE_WS_SEQUENCE_GAP_COUNT_TOTAL,
        EXCHANGE_WS_HEARTBEAT_MISS_COUNT_TOTAL,
        PAPER_SIM_FILL_COUNT_TOTAL,
        PAPER_SIM_SLIPPAGE_BPS,
    }
)


def to_prom(name: str) -> str:
    """Prometheus 메트릭 이름 형식으로 변환한다(`.` -> `_`)."""
    return name.replace(".", "_")
