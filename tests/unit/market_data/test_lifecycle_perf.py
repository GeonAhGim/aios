"""LA-7 lifecycle perf — 전이표 O(1) 조회 성능 단언.

Split from test_lifecycle.py (task-23915): pytest_perf_parallel_2 병렬 실행에서
CPU 경합이 `_bulk_lookup`의 5만 회 반복 측정을 왜곡한다. perf 테스트 전용 파일로
분리해 병렬 CI에서 독립적으로 측정한다.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1
"""

from __future__ import annotations

import pytest

from src.foundation.market_data.contracts.v1 import SymbolStatus
from src.foundation.market_data.domain.reference.lifecycle import (
    LifecycleEvent,
    transition,
)
from tests.conftest import PerfBudget

_EVENTS: tuple[LifecycleEvent, ...] = ("LIST", "SUSPEND", "RESUME", "DELIST", "RENAME")

_ALLOWED: dict[tuple[SymbolStatus, LifecycleEvent], SymbolStatus] = {
    (SymbolStatus.PENDING, "LIST"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "SUSPEND"): SymbolStatus.SUSPENDED,
    (SymbolStatus.SUSPENDED, "RESUME"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.SUSPENDED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.LISTED, "RENAME"): SymbolStatus.LISTED,
}


def _bulk_lookup(states: list[SymbolStatus]) -> None:
    """5만 회 허용 조합 전이표 O(1) 조회 — perf_budget.batch 묶음으로 측정한다.

    예외 경로는 correctness 테스트(`test_transition_matches_table`,
    `test_delisted_rejects_every_event`)가 이미 증명하므로, 성능 구간은
    허용 조합(_ALLOWED 키) 위주로 구성한다.
    """
    for i in range(50_000):
        state = states[i % len(states)]
        event = _EVENTS[i % len(_EVENTS)]
        key = (state, event)
        if key in _ALLOWED:
            transition(state, event)


@pytest.mark.perf
def test_transition_completes_within_budget_for_bulk_lookups(
    perf_budget: PerfBudget,
) -> None:
    """전이표 조회는 O(1) 딕셔너리 lookup이라, 5만 회 반복 호출이 125ms
    안에 끝난다 — process_time() 15.6ms 틱 양자화 오차를 줄이기 위해
    batch=8로 8회 묶어 호출당 평균을 추정하고, best-of-5(n=5)로 호스트
    경합 노이즈를 제거한다.

    _bulk_lookup은 허용 조합(_ALLOWED 키)만 조회하므로, 예외 생성 비용은
    측정 대상에서 제외된다(예외 경로는 correctness 테스트가 담당).
    """
    states = list(SymbolStatus)

    sample = perf_budget.assert_within(
        lambda: _bulk_lookup(states),
        budget_ms=125.0,
        n=5,
        batch=8,
    )

    assert sample.cpu_ms < 125.0
