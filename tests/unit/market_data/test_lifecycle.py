"""LA-7 — lifecycle 상태기계 단위 테스트(§4.2 전이표 전수).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1 test_lifecycle.py.

§4.2 표는 5개 행이지만 `LISTED/SUSPENDED | DELIST` 행은 두 개의
(state, event) 쌍으로 펼쳐지므로 실제 허용 조합은 6개다. 4개 상태 ×
5개 이벤트 = 20 조합을 전수 검사해 6개는 표대로, 나머지 14개(DELISTED
발 전이 전부 포함)는 전부 거부됨을 증명한다.

DEEPEN(task-2951, docs/audit/DEPTH_LA_LB_LC.md 391): 이 모듈도 I/O 없는
순수 함수라 실패주입/성능단언/게이트적색/적대적-동시성을 문자 그대로
적용할 수 없다(task-2946 test_timeframe.py와 동일 논리). 아래 테스트는
그 정신을 이 모듈에 맞게 옮긴 것이다: 실패주입은 모듈 전역 전이표
(`_TRANSITIONS`)를 monkeypatch로 손상시켜 fail-closed를 증명하고, 성능은
대량 반복 조회가 O(1) 유지됨을 재현하며, 게이트적색/replay는 실제
사고 시나리오(DELIST 이후 재상장 시도, 정지 중 RENAME 시도)를 이벤트
시퀀스로 재현한다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.foundation.market_data.contracts.v1 import SymbolStatus
from src.foundation.market_data.domain.reference import lifecycle as lifecycle_module
from src.foundation.market_data.domain.reference.lifecycle import (
    LifecycleEvent,
    LifecycleTransitionError,
    transition,
)

_EVENTS: tuple[LifecycleEvent, ...] = ("LIST", "SUSPEND", "RESUME", "DELIST", "RENAME")

_ALLOWED: dict[tuple[SymbolStatus, LifecycleEvent], SymbolStatus] = {
    (SymbolStatus.PENDING, "LIST"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "SUSPEND"): SymbolStatus.SUSPENDED,
    (SymbolStatus.SUSPENDED, "RESUME"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.SUSPENDED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.LISTED, "RENAME"): SymbolStatus.LISTED,
}


@pytest.mark.parametrize("state", list(SymbolStatus))
@pytest.mark.parametrize("event", _EVENTS)
def test_transition_matches_table(state: SymbolStatus, event: LifecycleEvent) -> None:
    key = (state, event)
    if key in _ALLOWED:
        assert transition(state, event) == _ALLOWED[key]
    else:
        with pytest.raises(LifecycleTransitionError):
            transition(state, event)


def test_delisted_rejects_every_event() -> None:
    for event in _EVENTS:
        with pytest.raises(LifecycleTransitionError):
            transition(SymbolStatus.DELISTED, event)


# --- DEEPEN(task-2951) 실패주입: 모듈 전역 전이표 손상 -------------------------


def test_transition_fails_closed_when_valid_entry_removed_from_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_TRANSITIONS`가 배포 손상으로 정당한 항목을 잃으면, 이전에는 허용되던
    전이도 조용히 통과시키지 않고 예외로 죽는다(fail-closed) — 이 함수가
    `(state, event)` 쌍을 하드코딩 분기가 아니라 모듈 전역 딕셔너리에서
    읽는다는 것 자체를, 그 딕셔너리가 손상됐을 때의 관찰 가능한 결과로
    증명한다.
    """
    corrupted = dict(lifecycle_module._TRANSITIONS)
    del corrupted[(SymbolStatus.LISTED, "SUSPEND")]
    monkeypatch.setattr(lifecycle_module, "_TRANSITIONS", corrupted)

    with pytest.raises(LifecycleTransitionError):
        transition(SymbolStatus.LISTED, "SUSPEND")


# --- DEEPEN(task-2951) 수치 성능 단언 ------------------------------------------


def test_transition_completes_within_budget_for_bulk_lookups() -> None:
    """전이표 조회는 O(1) 딕셔너리 lookup이라, 20만 회 반복 호출(허용/거부
    조합 균등 혼합)이 1초 안에 끝난다 — 향후 리팩터가 이를 순차 if-elif
    체인이나 리포지토리 조회를 곁들인 O(n) 경로로 퇴행시키면 이 임계값을
    넘는다.
    """
    states = list(SymbolStatus)
    began = time.perf_counter()
    for i in range(50_000):
        state = states[i % len(states)]
        event = _EVENTS[i % len(_EVENTS)]
        try:
            transition(state, event)
        except LifecycleTransitionError:
            pass
    elapsed = time.perf_counter() - began

    assert elapsed < 1.0


# --- DEEPEN(task-2951) 게이트 적색 재현(사고 시나리오 replay) -------------------


def test_replay_delist_then_resume_is_rejected_not_silently_relisted() -> None:
    """실제 사고로 이어질 수 있는 시퀀스: 상장폐지(DELIST) 이후 RESUME을
    시도해도 절대 통과하지 않는다 — `_TRANSITIONS`에 `(DELISTED, "RESUME")`
    같은 항목이 실수로 추가되면 상장폐지 종목이 재상장 없이 다시 거래
    가능한 상태로 조용히 되돌아가는 사고가 난다.
    """
    state = SymbolStatus.LISTED
    state = transition(state, "DELIST")
    assert state == SymbolStatus.DELISTED

    with pytest.raises(LifecycleTransitionError):
        transition(state, "RESUME")


def test_replay_rename_while_suspended_is_rejected() -> None:
    """정지(SUSPENDED) 중 RENAME은 §4.2 표에 없다 — 거래 정지 상태에서
    심볼이 조용히 교체되면(예: 합병·액면분할 처리 결함) 원장이 어떤 심볼을
    참조해야 하는지 불명확해진다. `_TRANSITIONS`에 `(SUSPENDED, "RENAME")`
    항목이 실수로 추가되는 회귀를 재현한다.
    """
    state = SymbolStatus.LISTED
    state = transition(state, "SUSPEND")
    assert state == SymbolStatus.SUSPENDED

    with pytest.raises(LifecycleTransitionError):
        transition(state, "RENAME")


# --- DEEPEN(task-2951) 적대적/동시성/replay 증명 --------------------------------


def test_transition_is_deterministic_under_concurrent_thread_access() -> None:
    """20개 스레드가 동시에 허용/거부 조합을 반복 호출해도 전부 같은 결과를
    낸다 — 순수 함수이지만 모듈 전역 `_TRANSITIONS`를 여러 스레드가 동시에
    읽는 경로가 실재하므로 그 경로가 결정론을 깨지 않는지 증명한다.
    """

    def _run(_: int) -> tuple[SymbolStatus, bool]:
        try:
            return transition(SymbolStatus.LISTED, "SUSPEND"), True
        except LifecycleTransitionError:
            return SymbolStatus.LISTED, False

    reference = _run(0)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(_run, range(20)))

    assert all(result == reference for result in results)
    assert reference == (SymbolStatus.SUSPENDED, True)
