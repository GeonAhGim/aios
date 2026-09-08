"""L4-27 — §7.2 OMS/exchange/paper_sim 메트릭 이름 등재 전수 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.2, §9 L4-27
(`§6 정규식 `aios.<context>.<subject>.<verb>` 전수 통과`).

PLT-04 단일 출처(`metric_names.py`)를 그대로 재사용한다 — 이 파일은 새
레지스트리를 만들지 않고, L4-27이 등재한 §7.2 상수까지 포함해 `ALL_METRIC_NAMES`
전수가 정규식을 만족하는지, 그리고 규칙을 어긴 이름을 넣으면 실제로
실패하는지(반증)를 함께 단언한다.
"""
from __future__ import annotations

import re

from src.core.observability import metric_names

_METRIC_NAME_RE = re.compile(
    r"^aios\.[a-z_]+\.[a-z_]+\.[a-z_]+(_total|_seconds|_bytes|\.gauge)?$"
)

# §7.2 원문 그대로라 4세그먼트라서 정규식을 위반하는 이름들 — metric_names.py가
# 이걸 그대로 등재하지 않고 정규화했음을 반증으로 확인한다.
_SPEC_LITERAL_VIOLATIONS = (
    "aios.oms.outbox.dispatch.duration_seconds",
    "aios.oms.inbox.duplicate.count_total",
    "aios.oms.reconcile.run.count_total",
    "aios.oms.algo.slice_submit.count_total",
    "aios.exchange.ws.reconnect.count_total",
    "aios.oms.unknown_orders",  # 2세그먼트뿐이라 위반
    "aios.exchange.clock_offset_ms",  # 2세그먼트뿐이라 위반
)


def test_all_metric_names_match_naming_regex() -> None:
    violations = [n for n in metric_names.ALL_METRIC_NAMES if not _METRIC_NAME_RE.match(n)]
    assert violations == [], f"메트릭 이름 형식 위반: {violations}"


def test_naming_regex_rejects_spec_literal_4segment_names() -> None:
    """반증 — §7.2 원문(4세그먼트/2세그먼트)은 정규식이 실제로 거부해야 한다."""
    for literal in _SPEC_LITERAL_VIOLATIONS:
        assert not _METRIC_NAME_RE.match(literal), f"위반 이름이 통과했다: {literal}"
        assert literal not in metric_names.ALL_METRIC_NAMES


def test_l4_27_oms_exchange_paper_sim_names_are_registered() -> None:
    """§7.2 표에 나열된 12(oms)+8(exchange)+2(paper_sim) 항목이 전부 등재됐는지."""
    expected = {
        metric_names.OMS_ORDER_SUBMIT_COUNT_TOTAL,
        metric_names.OMS_ORDER_SUBMIT_DURATION_SECONDS,
        metric_names.OMS_ORDER_TRANSITION_COUNT_TOTAL,
        metric_names.OMS_OUTBOX_BACKLOG_GAUGE,
        metric_names.OMS_OUTBOX_DISPATCH_DURATION_SECONDS,
        metric_names.OMS_INBOX_DUPLICATE_COUNT_TOTAL,
        metric_names.OMS_INBOX_LAG_SECONDS_GAUGE,
        metric_names.OMS_UNKNOWN_ORDERS_GAUGE,
        metric_names.OMS_UNKNOWN_RESOLUTION_DURATION_SECONDS,
        metric_names.OMS_RECONCILE_RUN_COUNT_TOTAL,
        metric_names.OMS_RECONCILE_LAG_SINCE_HEALTHY_SECONDS_GAUGE,
        metric_names.OMS_ALGO_SLICE_SUBMIT_COUNT_TOTAL,
        metric_names.EXCHANGE_HTTP_REQUEST_COUNT_TOTAL,
        metric_names.EXCHANGE_HTTP_REQUEST_DURATION_SECONDS,
        metric_names.EXCHANGE_RATE_LIMIT_WAIT_SECONDS,
        metric_names.EXCHANGE_CIRCUIT_STATE_GAUGE,
        metric_names.EXCHANGE_CLOCK_OFFSET_MS_GAUGE,
        metric_names.EXCHANGE_WS_RECONNECT_COUNT_TOTAL,
        metric_names.EXCHANGE_WS_SEQUENCE_GAP_COUNT_TOTAL,
        metric_names.EXCHANGE_WS_HEARTBEAT_MISS_COUNT_TOTAL,
        metric_names.PAPER_SIM_FILL_COUNT_TOTAL,
        metric_names.PAPER_SIM_SLIPPAGE_BPS,
    }
    assert expected <= metric_names.ALL_METRIC_NAMES
    assert len(expected) == 22


def test_all_metric_names_are_nonempty_and_unique() -> None:
    names = list(metric_names.ALL_METRIC_NAMES)
    assert len(names) > 0
    assert len(names) == len(set(names))
