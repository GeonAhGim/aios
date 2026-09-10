"""DC-3 domain/instruments/lifecycle.py — DEEPEN(task-2875, DEPTH_DC_RD.md#1125)
D1 -> D3 증빙.

기존 test_lifecycle.py는 24개 (state, event) 조합 전수 파라미터화 +
delisted negative만 증명했다(D1) — 감사에서 "실패주입 없음, 성능단언 없음,
게이트적색 재현 없음, D3 요소 없음"으로 지적됐다(docs/audit/DEPTH_DC_RD.md
1125행). 이 파일이 그 부족분을 채운다. 새 기능은 추가하지 않는다 —
`transition`/`audit_event_for`는 순수 함수라 게이트·리포지토리가 없으므로,
이 파일에서 "게이트"란 그 두 함수 자체를 실제 다단계 생애주기 시나리오에
연쇄 적용한 호출자 시뮬레이션을 뜻한다(§4.2 표가 곧 게이트 정의다).

1. 실패 주입 — 타입 힌트(Literal)가 런타임을 강제하지 않으므로, 표에 없는
   임의 문자열/None/다른 타입의 (state, event)를 주입해도 예외 없이 통과
   시키거나 임의값을 반환하지 않고 항상 fail-closed로 거부됨을 증명한다.
2. 성능 단언 — transition/audit_event_for 반복 호출이 절대시간 예산 내에
   있다(딕셔너리 조회이므로 회귀가 있다면 O(n) 선형탐색 등으로의 퇴화다).
3. 게이트 적색 재현 — 실제 다단계 생애주기 시퀀스(발행~상장폐지)를 재생하며
   각 단계에서 허용 전이는 통과하고, 시퀀스 중간에 주입한 불법 전이는
   막히며 그 실패가 상태를 변형하지 않음(호출자가 반환값을 버리면 그만인
   순수성)을 증명한다.
4. 리플레이 결정론 + 동시 다중 인스턴스(D3) — 같은 입력 재호출이 항상 같은
   결과이고, 여러 스레드가 서로 다른 (state, event)로 동시에 호출해도
   서로 오염시키지 않는다(모듈 전역 가변 상태 없음의 동시성 증거).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from src.foundation.market_data.contracts.v2.instruments import InstrumentLifecycle
from src.foundation.market_data.domain.instruments.lifecycle import (
    AUDIT_EVENT_INSTRUMENT_DELISTED,
    AUDIT_EVENT_INSTRUMENT_HALTED,
    AUDIT_EVENT_INSTRUMENT_LISTED,
    AUDIT_EVENT_INSTRUMENT_RESUMED,
    AUDIT_EVENT_LISTING_REPLACED,
    LifecycleTransitionError,
    RelistRequiresNewInstrumentError,
    audit_event_for,
    transition,
)

# ---- 실패 주입(D2) — 표에 없는 임의 입력이 fail-closed로 거부됨 ----

_GARBAGE_STATES: tuple[Any, ...] = (
    "SUSPENDED",  # LA-7 계약(v1)의 상태 이름 — DC-3(v2)와 혼동 주입
    "active",  # 대소문자 오염
    "",
    None,
    123,
    ("ACTIVE",),
)

_GARBAGE_EVENTS: tuple[Any, ...] = (
    "unlisted",
    "LISTED",  # 대소문자 오염
    "",
    None,
    123,
    "rename",  # LA-7 계약(v1)의 이벤트 이름 — DC-3(v2)와 혼동 주입
)


@pytest.mark.parametrize("garbage_state", _GARBAGE_STATES)
def test_transition_rejects_garbage_state_fail_closed(garbage_state: Any) -> None:
    """Literal/Enum 타입힌트는 런타임에 강제되지 않는다 — mypy를 우회하는
    호출자(예: 역직렬화 경로)가 표에 없는 state를 주입해도 조용히 통과하거나
    임의 다음 state를 반환하지 않고 항상 거부돼야 한다(fail-closed)."""
    with pytest.raises(LifecycleTransitionError):
        transition(garbage_state, "listed")


@pytest.mark.parametrize("garbage_event", _GARBAGE_EVENTS)
def test_transition_rejects_garbage_event_fail_closed(garbage_event: Any) -> None:
    with pytest.raises(LifecycleTransitionError):
        transition(InstrumentLifecycle.ACTIVE, garbage_event)


@pytest.mark.parametrize("garbage_state", _GARBAGE_STATES)
@pytest.mark.parametrize("garbage_event", _GARBAGE_EVENTS)
def test_transition_rejects_garbage_cross_product_fail_closed(
    garbage_state: Any, garbage_event: Any
) -> None:
    """가비지 state × 가비지 event 곱집합 전체가 예외 없는 통과나 크래시
    (KeyError/TypeError 누출) 없이 전부 `LifecycleTransitionError` 하나로
    수렴함을 증명한다 — 호출자가 잡을 예외 타입이 하나뿐이라는 계약."""
    with pytest.raises(LifecycleTransitionError):
        transition(garbage_state, garbage_event)


@pytest.mark.parametrize("garbage_state", _GARBAGE_STATES)
@pytest.mark.parametrize("garbage_event", _GARBAGE_EVENTS)
def test_audit_event_for_rejects_garbage_cross_product_fail_closed(
    garbage_state: Any, garbage_event: Any
) -> None:
    with pytest.raises(LifecycleTransitionError):
        audit_event_for(garbage_state, garbage_event)


def test_transition_never_returns_unhashable_lookup_crash() -> None:
    """리스트처럼 unhashable한 state를 넣으면 `dict.__getitem__`이
    `TypeError`(unhashable type)를 던져야 정상인데, 이 모듈은 이를 잡아
    `LifecycleTransitionError`로 통일해 호출자가 예외 타입을 하나만 알면
    되게 한다 — 내부 구현(dict)이 새는 것을 막는 방어."""
    with pytest.raises(LifecycleTransitionError):
        transition([InstrumentLifecycle.ACTIVE], "listed")


# ---- 성능 단언(D2) ----


@pytest.mark.perf
def test_transition_and_audit_event_for_meet_latency_budget() -> None:
    """순수 딕셔너리 조회이므로 매우 빨라야 한다 — 절대시간 예산은 넉넉히
    잡아(느린 CI 머신 대비) 회귀(예: 선형탐색으로의 퇴화)만 잡는다."""
    iterations = 20_000
    budget_sec = 1.0  # 실측 로컬 <0.05s
    calls = [
        (InstrumentLifecycle.PENDING, "listed"),
        (InstrumentLifecycle.ACTIVE, "symbol_changed"),
        (InstrumentLifecycle.ACTIVE, "halted"),
        (InstrumentLifecycle.HALTED, "resumed"),
        (InstrumentLifecycle.ACTIVE, "delisted"),
    ]
    start = time.perf_counter()
    for _ in range(iterations):
        for state, event in calls:
            transition(state, event)
            audit_event_for(state, event)
    elapsed = time.perf_counter() - start
    total_calls = iterations * len(calls) * 2
    print(
        f"[DC-3 lifecycle] {total_calls} calls in {elapsed:.3f}s "
        f"({elapsed / total_calls * 1e6:.2f} us/call, budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"transition/audit_event_for {total_calls}회가 예산({budget_sec}s)을 "
        f"넘었습니다({elapsed:.3f}s) — 딕셔너리 조회가 선형탐색으로 퇴화했는지 "
        "확인하세요."
    )


# ---- 게이트 적색 재현(D2) — 실제 다단계 생애주기 시퀀스 재생 ----


def test_gate_red_blocks_illegal_transition_mid_replay_without_mutating_flow() -> None:
    """발행 -> 상장 -> 심볼변경 -> 정지 -> (불법: 곧바로 상장폐지 재이벤트
    주입) -> 재개 -> 상장폐지 순서를 재생한다. 불법 지점에서 게이트가
    적색(예외)이 되고, 호출자가 그 반환값을 쓰지 않으면 시퀀스의 나머지가
    직전 합법 상태(HALTED)를 기준으로 정상 이어짐을 증명한다 — 실패한 호출이
    이후 호출에 부작용을 남기지 않는다는 순수성 증거."""
    state = InstrumentLifecycle.PENDING

    state = transition(state, "listed")
    assert state == InstrumentLifecycle.ACTIVE

    state = transition(state, "symbol_changed")
    assert state == InstrumentLifecycle.ACTIVE

    state = transition(state, "halted")
    assert state == InstrumentLifecycle.HALTED

    # 불법 주입: HALTED 상태에서 symbol_changed는 표에 없다(ACTIVE 전용).
    with pytest.raises(LifecycleTransitionError):
        transition(state, "symbol_changed")
    # 게이트가 막았으므로 호출자는 반환값을 쓰지 않는다 — state는 불변.
    assert state == InstrumentLifecycle.HALTED

    state = transition(state, "resumed")
    assert state == InstrumentLifecycle.ACTIVE

    state = transition(state, "delisted")
    assert state == InstrumentLifecycle.DELISTED

    # 상장폐지 후 재상장 이벤트는 in-place 전이가 아니라 새 instrument
    # 발급 신호다 — 게이트가 별도 타입 예외로 적색을 내고, 원 state는 그대로.
    with pytest.raises(RelistRequiresNewInstrumentError):
        transition(state, "relisted")
    assert state == InstrumentLifecycle.DELISTED

    # DELISTED에서 그 외 모든 이벤트도 전부 적색 — 종단 상태 고정 증명.
    for event in ("listed", "symbol_changed", "halted", "resumed", "delisted"):
        with pytest.raises(LifecycleTransitionError):
            transition(state, event)
    assert state == InstrumentLifecycle.DELISTED


def test_gate_red_audit_event_matches_transition_outcome_along_happy_path() -> None:
    """게이트가 적색이 아닌(전이가 허용된) 매 단계에서 `audit_event_for`가
    `transition`과 같은 (state, event) 판정 기준으로 정확히 대응하는 감사
    이벤트 이름을 낸다 — 두 함수의 판정이 어긋나 감사 로그 없이 전이가
    통과하는 구멍이 없음을 증명한다."""
    sequence: list[tuple[InstrumentLifecycle, str, str]] = [
        (InstrumentLifecycle.PENDING, "listed", AUDIT_EVENT_INSTRUMENT_LISTED),
        (InstrumentLifecycle.ACTIVE, "symbol_changed", AUDIT_EVENT_LISTING_REPLACED),
        (InstrumentLifecycle.ACTIVE, "halted", AUDIT_EVENT_INSTRUMENT_HALTED),
        (InstrumentLifecycle.HALTED, "resumed", AUDIT_EVENT_INSTRUMENT_RESUMED),
        (InstrumentLifecycle.ACTIVE, "delisted", AUDIT_EVENT_INSTRUMENT_DELISTED),
    ]
    state = InstrumentLifecycle.PENDING
    for expected_from, event, expected_audit in sequence:
        assert state == expected_from
        assert audit_event_for(state, event) == expected_audit
        state = transition(state, event)


# ---- 리플레이 결정론 + 동시 다중 인스턴스(D3) ----

_ALL_LEGAL_CALLS: tuple[tuple[InstrumentLifecycle, str], ...] = (
    (InstrumentLifecycle.PENDING, "listed"),
    (InstrumentLifecycle.ACTIVE, "symbol_changed"),
    (InstrumentLifecycle.ACTIVE, "halted"),
    (InstrumentLifecycle.HALTED, "resumed"),
    (InstrumentLifecycle.ACTIVE, "delisted"),
    (InstrumentLifecycle.HALTED, "delisted"),
)


def test_replay_is_deterministic_for_transition_and_audit_event_for() -> None:
    """같은 (state, event)를 두 번 호출해도 완전히 동일한 결과 — 숨은
    시계·난수·전역 가변 상태가 없다는 재생(replay) 안전성 증거."""
    for state, event in _ALL_LEGAL_CALLS:
        first = transition(state, event)
        second = transition(state, event)
        assert first == second

        first_audit = audit_event_for(state, event)
        second_audit = audit_event_for(state, event)
        assert first_audit == second_audit


def test_concurrent_instances_do_not_cross_contaminate() -> None:
    """서로 다른 (state, event) 입력을 가진 다중 워커(스레드, 동시 다중
    인스턴스 시뮬레이션)가 같은 모듈 함수를 동시에 호출해도 서로의 결과를
    오염시키지 않는다 — 모듈 레벨 가변 상태가 없다는 동시성 증거(D3)."""

    def _run(
        pair: tuple[InstrumentLifecycle, str],
    ) -> tuple[tuple[InstrumentLifecycle, str], InstrumentLifecycle, str]:
        state, event = pair
        result_state = transition(state, event)
        result_audit = audit_event_for(state, event)
        return pair, result_state, result_audit

    expected_states = {
        (InstrumentLifecycle.PENDING, "listed"): InstrumentLifecycle.ACTIVE,
        (InstrumentLifecycle.ACTIVE, "symbol_changed"): InstrumentLifecycle.ACTIVE,
        (InstrumentLifecycle.ACTIVE, "halted"): InstrumentLifecycle.HALTED,
        (InstrumentLifecycle.HALTED, "resumed"): InstrumentLifecycle.ACTIVE,
        (InstrumentLifecycle.ACTIVE, "delisted"): InstrumentLifecycle.DELISTED,
        (InstrumentLifecycle.HALTED, "delisted"): InstrumentLifecycle.DELISTED,
    }
    expected_audits = {
        (InstrumentLifecycle.PENDING, "listed"): AUDIT_EVENT_INSTRUMENT_LISTED,
        (InstrumentLifecycle.ACTIVE, "symbol_changed"): AUDIT_EVENT_LISTING_REPLACED,
        (InstrumentLifecycle.ACTIVE, "halted"): AUDIT_EVENT_INSTRUMENT_HALTED,
        (InstrumentLifecycle.HALTED, "resumed"): AUDIT_EVENT_INSTRUMENT_RESUMED,
        (InstrumentLifecycle.ACTIVE, "delisted"): AUDIT_EVENT_INSTRUMENT_DELISTED,
        (InstrumentLifecycle.HALTED, "delisted"): AUDIT_EVENT_INSTRUMENT_DELISTED,
    }

    workload = list(_ALL_LEGAL_CALLS) * 50  # 300회 동시 호출, 서로 다른 입력이 반복 교차
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_run, workload))

    assert len(results) == len(workload)
    for pair, result_state, result_audit in results:
        assert result_state == expected_states[pair], (
            f"{pair} 스레드가 다른 입력의 전이 결과와 섞였다: {result_state}"
        )
        assert result_audit == expected_audits[pair], (
            f"{pair} 스레드가 다른 입력의 감사 이벤트와 섞였다: {result_audit}"
        )
