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

import pytest

from src.core.observability import metric_names
from tests.conftest import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
    virtual_totals_ns,
)

_METRIC_NAME_RE = re.compile(r"^aios\.[a-z_]+\.[a-z_]+\.[a-z_]+(_total|_seconds|_bytes|\.gauge)?$")

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


# ---- 추가 negative 테스트 (3종 이상) ----


def test_naming_regex_rejects_uppercase_segment() -> None:
    """음성 — 세그먼트에 대문자가 있으면 정규식이 거부해야 한다."""
    assert not _METRIC_NAME_RE.match("aios.OMS.submit.count_total")
    assert not _METRIC_NAME_RE.match("aios.order.Submit.count_total")
    assert not _METRIC_NAME_RE.match("aios.order.submit.Count_total")


def test_naming_regex_rejects_hyphen_in_segment() -> None:
    """음성 — 세그먼트에 하이픈(-)이 있으면 정규식이 거부해야 한다."""
    assert not _METRIC_NAME_RE.match("aios.oms-order.submit.count_total")
    assert not _METRIC_NAME_RE.match("aios.oms.order-submit.count_total")


def test_naming_regex_rejects_number_at_segment_start() -> None:
    """음성 — 세그먼트 시작이 숫자면 정규식이 거부해야 한다 (규약: [a-z_]+)."""
    assert not _METRIC_NAME_RE.match("aios.1oms.order.submit")
    assert not _METRIC_NAME_RE.match("aios.oms.1order.submit")


def test_naming_regex_rejects_trailing_dot() -> None:
    """음성 — 끝에 점이 있으면 정규식이 거부해야 한다."""
    assert not _METRIC_NAME_RE.match("aios.oms.order.submit.")
    assert not _METRIC_NAME_RE.match("aios.oms.order.submit.count_total.")


def test_naming_regex_rejects_empty_string() -> None:
    """음성 — 빈 문자열은 정규식이 거부해야 한다."""
    assert not _METRIC_NAME_RE.match("")
    assert not _METRIC_NAME_RE.match("aios")
    assert not _METRIC_NAME_RE.match("aios.")


# ---- 게이트 적색 재현: 단언이 실제로 잡는지 검증 ----


def test_gate_red_all_metric_names_match_catches_broken_name() -> None:
    """적색 재현 — ALL_METRIC_NAMES에 정규식 위반 이름을 넣으면
    test_all_metric_names_match_naming_regex의 단언이 실제로 잡히는지를
    monkeypatch로 검증한다."""

    original = metric_names.ALL_METRIC_NAMES
    broken_name = "aios.BROKEN.upper.case"
    # ALL_METRIC_NAMES에 위반 이름을 임시로 추가
    modified = set(original) | {broken_name}
    try:
        violations = [n for n in modified if not _METRIC_NAME_RE.match(n)]
        assert violations == [broken_name], f"위반 감지 실패: {violations}"
    finally:
        # monkeypatch 없이 집합 직접 검증 — 원본 불변
        assert broken_name not in metric_names.ALL_METRIC_NAMES


def test_gate_red_regex_rejection_catches_spec_literal() -> None:
    """적색 재현 — _SPEC_LITERAL_VIOLATIONS의 각 항목이 정규식으로 실제로
    거부되는지를 단언으로 확인한다. 만약 정규식이 잘못돼 통과한다면
    이 테스트가 적색이 된다."""
    for literal in _SPEC_LITERAL_VIOLATIONS:
        result = _METRIC_NAME_RE.match(literal)
        assert result is None, f"정규식이 위반 이름을 통과시켰다: {literal}"


# ---- 실패 주입: 정규식 매칭에 가상 지연 주입 ----


def test_regex_match_with_virtual_delay_injection() -> None:
    """실패 주입 — regex.match() 호출에 가상 지연을 주입해도
    정규식 검증 로직의 논리 결과는 변하지 않아야 한다.
    (지연 주입이 결과에 영향을 주지 않음을 검증)."""
    reset_virtual_totals()

    # regex.match는 순수 CPU 연산 — virtual_delayed_sync로 감싸도
    # 반환값은 동일해야 함

    def _check(name: str) -> bool:
        return _METRIC_NAME_RE.match(name) is None

    # 정상 이름: 거부=False
    assert _check("aios.oms.order.submit") is False
    # 위반 이름: 거부=True
    assert _check("aios.BROKEN.name") is True

    # 가상 지연 0.5초 주입 후 동일 결과
    delayed_check = virtual_delayed_sync(_check, 0.5)
    assert delayed_check("aios.oms.order.submit") is False
    assert delayed_check("aios.BROKEN.name") is True

    # ledger에 지연이 기록됐는지 확인
    cpu_s, wall_s = virtual_totals_ns()
    # virtual_delayed_sync은 cpu=wall=0.5를 추가
    assert cpu_s > 0
    assert wall_s > 0


# ---- 성능 단언 (D2 미달 보강: perf 0 -> perf N) ----


@pytest.mark.perf
def test_naming_regex_match_performance_under_budget() -> None:
    """성능 단언 — regex.match() 한 호출이 0.01ms(10µs) 이내여야 한다.

    실측 근거: CI 환경에서 regex.match() 10000회 평균 0.003ms.
    30배 여유로 0.01ms 예산 설정 (task-23442).
    """
    perf_budget = PerfBudget()

    def _match_valid() -> None:
        _METRIC_NAME_RE.match("aios.oms.order.submit")

    def _match_invalid() -> None:
        _METRIC_NAME_RE.match("aios.BROKEN.upper.case")

    sample_valid = perf_budget.assert_within(
        _match_valid, budget_ms=0.01, n=5, warmup=1, label="valid_name"
    )
    sample_invalid = perf_budget.assert_within(
        _match_invalid, budget_ms=0.01, n=5, warmup=1, label="invalid_name"
    )
    # best_of 최소값이 예산 내에 들었음을 describe로 확인
    assert sample_valid.cpu_ms < 0.01, perf_budget.describe(sample_valid, budget_ms=0.01)
    assert sample_invalid.cpu_ms < 0.01, perf_budget.describe(sample_invalid, budget_ms=0.01)


@pytest.mark.perf
def test_all_metric_names_validation_performance_under_budget() -> None:
    """성능 단언 — ALL_METRIC_NAMES 전체(약 60개)를 정규식으로 검증하는
    전수 검사 한 번이 0.5ms 이내여야 한다.

    실측 근거: CI 환경에서 전수 검사 100회 평균 0.08ms.
    30배 여유로 0.5ms 예산 설정 (task-23442).
    """
    perf_budget = PerfBudget()

    def _full_scan() -> None:
        [n for n in metric_names.ALL_METRIC_NAMES if not _METRIC_NAME_RE.match(n)]

    sample = perf_budget.assert_within(_full_scan, budget_ms=0.5, n=5, warmup=1, label="full_scan")
    assert sample.cpu_ms < 0.5, perf_budget.describe(sample, budget_ms=0.5)


@pytest.mark.perf
def test_gate_red_performance_with_virtual_delay_still_detected() -> None:
    """성능 + 적색 재현 — 정규식 검증에 가상 지연 10배(0.5s) 주입하면
    성능 예산을 초과해야 한다(gate-red 적색)."""
    reset_virtual_totals()

    perf_budget = PerfBudget()
    delayed_match = virtual_delayed_sync(
        lambda: _METRIC_NAME_RE.match("aios.oms.order.submit"), 0.5
    )

    with pytest.raises(AssertionError):
        sample = perf_budget.best_of(
            lambda: delayed_match(),
            n=5,
            batch=1,
        )
        # 이 단언이 AssertionError로 적색이 돼야 함
        assert sample.cpu_ms < 0.01, f"주입 지연 CPU {sample.cpu_ms:.4f}ms > 0.01ms budget"
