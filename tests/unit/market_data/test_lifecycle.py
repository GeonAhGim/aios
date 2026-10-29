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
from typing import Never

import pytest

from src.foundation.market_data.contracts.v1 import SymbolStatus
from src.foundation.market_data.domain.reference import lifecycle as lifecycle_module
from src.foundation.market_data.domain.reference.lifecycle import (
    LifecycleEvent,
    LifecycleTransitionError,
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
    """5만 회 전이표 조회 — perf_budget.batch 묶음으로 측정한다."""
    for i in range(50_000):
        state = states[i % len(states)]
        event = _EVENTS[i % len(_EVENTS)]
        try:
            transition(state, event)
        except LifecycleTransitionError:
            pass


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


def test_pending_rejects_non_list_events() -> None:
    """PENDING 상태는 LIST 이벤트만 허용한다 — SUSPEND/RESUME/DELIST/RENAME
    이 조용히 통과하면, 상장 전 심볼이 거래 정지·상장폐지·이름바꿈 상태를
    진입할 수 있어 원장이 불일치한다. (I-07 hard-fail 조건)
    """
    for event in _EVENTS:
        if event == "LIST":
            assert transition(SymbolStatus.PENDING, event) == SymbolStatus.LISTED
        else:
            with pytest.raises(LifecycleTransitionError):
                transition(SymbolStatus.PENDING, event)


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


def test_transition_fails_closed_when_table_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_TRANSITIONS` 전체가 비어 있으면, 정당한 LIST 전이조차 예외로
    죽는다 — fail-closed가 전이표 공백에서도 작동함을 증명한다.
    """
    monkeypatch.setattr(lifecycle_module, "_TRANSITIONS", {})

    with pytest.raises(LifecycleTransitionError):
        transition(SymbolStatus.PENDING, "LIST")


# --- DEEPEN(task-2951) 수치 성능 단언 ------------------------------------------


@pytest.mark.perf
def test_transition_completes_within_budget_for_bulk_lookups(
    perf_budget: PerfBudget,
) -> None:
    """전이표 조회는 O(1) 딕셔너리 lookup이라, 5만 회 반복 호출(허용/거부
    조합 균등 혼합)이 125ms 안에 끝난다 — process_time() 15.6ms 틱 양자화
    오차를 줄이기 위해 batch=8로 8회 묶어 호출당 평균을 추정하고,
    best-of-5(n=5)로 호스트 경합 노이즈를 제거한다.
    """
    states = list(SymbolStatus)

    sample = perf_budget.assert_within(
        lambda: _bulk_lookup(states),
        budget_ms=125.0,
        n=5,
        batch=8,
    )

    assert sample.cpu_ms < 125.0


# --- DEEPEN(task-2951) 실패주입: 지연 주입 시 적색 --------------------------


def test_perf_budget_goes_red_when_transition_is_cpu_delayed(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """성능 단언이 허울이 아닌지 확인: `_TRANSITIONS`를 CPU 지연 맵으로
    교체하면 기존 125ms 예산 단언이 적색으로 전환되어야 한다. —
    `time.process_time()` 기반이므로 `sleep`이 아닌 실제 CPU 부하를
    주입한다. 지연 맵은 500회 조회에 ~50ms를 더하므로(원본 ~1ms 대비
    50배), 125ms 예산을 쉽게 초과한다.
    """

    class _SlowMapping:
        """`_TRANSITIONS` 대신 들어갈 지연 맵 — 모든 조회에 CPU 부하를 더한다."""

        def __init__(self) -> None:
            self._data = dict(lifecycle_module._TRANSITIONS)

        def __getitem__(self, key: object) -> SymbolStatus:
            # 0.3ms CPU 부하: arithmetic loop가 process_time()을 실제로 증가시킴
            target = time.perf_counter() + 0.0003
            while time.perf_counter() < target:
                _ = sum(range(100))
            return self._data[key]

        def __contains__(self, key: object) -> bool:
            return key in self._data

    slow_map = _SlowMapping()
    monkeypatch.setattr(lifecycle_module, "_TRANSITIONS", slow_map)

    # 500회 조회: 500 × 0.1ms = 50ms 부하 → 원본 ~0.5ms 대비 100배
    def _small_bulk_lookup() -> None:
        states = list(SymbolStatus)
        for i in range(500):
            state = states[i % len(states)]
            event = _EVENTS[i % len(_EVENTS)]
            try:
                transition(state, event)
            except LifecycleTransitionError:
                pass

    # 지연 주입 버전 — 예산을 초과해야 한다(단언이 제대로 작동함을 증명)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _small_bulk_lookup,
            budget_ms=125.0,
            n=3,
            batch=8,
        )


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


# --- 추가 negative test: 불변식 위반 입력 ------------------------------


def test_listed_rejects_list_event() -> None:
    """이미 상장(LISTED)된 심볼에 LIST 이벤트를 보내면 거부해야 한다 —
    상장 심볼이 재상장 상태로 전환되는 것은 도메인 불변식(I-07)에 위배된다.
    `_TRANSITIONS`에 `(LISTED, "LIST")`가 실수로 추가되는 회귀를 검증한다.
    """
    with pytest.raises(LifecycleTransitionError):
        transition(SymbolStatus.LISTED, "LIST")


def test_suspended_rejects_list_and_suspend_events() -> None:
    """거래 정지(SUSPENDED) 상태에서는 LIST/SUSPEND 이벤트가 모두 거부된다 —
    정지 종목이 갑자기 상장 상태로 전환되거나 중복 정지되는 것은 원장
    불일치(I-07)를 유발한다.
    """
    for event in ("LIST", "SUSPEND"):
        with pytest.raises(LifecycleTransitionError):
            transition(SymbolStatus.SUSPENDED, event)


# --- 추가 실패주입: _TRANSITIONS 접근 시 예외 유발 -------------------------


def test_transition_raises_when_transition_table_raises_on_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_TRANSITIONS.__getitem__`가 예외를 raise하면(예: 메모리 손상,
    분산 캐시 장애), transition()은 예외를 그대로 전파한다(fail-closed).
    이 함수가 `_TRANSITIONS[(state, event)]` 경로를 통해 읽는다는 것을
    직접 관찰한다.
    """

    # _TRANSITIONS 대신 __getitem__이 항상 예외를 raise하는 객체로 교체
    class BrokenMapping:
        def __getitem__(self, key: object) -> Never:
            raise RuntimeError("internal error")

        def __contains__(self, key: object) -> bool:
            return False

    monkeypatch.setattr(lifecycle_module, "_TRANSITIONS", BrokenMapping(), raising=False)

    with pytest.raises(RuntimeError, match="internal error"):
        transition(SymbolStatus.LISTED, "SUSPEND")
