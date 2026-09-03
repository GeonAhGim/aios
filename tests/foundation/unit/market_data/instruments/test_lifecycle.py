"""DC-3 — instruments/lifecycle 상태기계 단위 테스트(§4.2 전이표 전수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§4.2, §9.2 DC-3.

§4.2 표는 6행이지만 `active/halted + delisted` 행은 두 개의 (state, event)
쌍으로 펼쳐지므로 in-place 전이는 6개, "새 instrument 발급" 신호(관계
delisted+relisted)까지 합치면 7개 조합이 표에 정의돼 있다. 4개 state ×
6개 event = 24 조합을 전수 검사해 이 7개는 표대로, 나머지 17개는 전부
거부됨을 증명한다.
"""
from __future__ import annotations

import pytest

from src.foundation.market_data.contracts.v2.instruments import InstrumentLifecycle
from src.foundation.market_data.domain.instruments.lifecycle import (
    AUDIT_EVENT_INSTRUMENT_DELISTED,
    AUDIT_EVENT_INSTRUMENT_HALTED,
    AUDIT_EVENT_INSTRUMENT_LISTED,
    AUDIT_EVENT_INSTRUMENT_RELISTED,
    AUDIT_EVENT_INSTRUMENT_RESUMED,
    AUDIT_EVENT_LISTING_REPLACED,
    LifecycleEvent,
    LifecycleTransitionError,
    RelistRequiresNewInstrumentError,
    audit_event_for,
    transition,
)

_EVENTS: tuple[LifecycleEvent, ...] = (
    "listed",
    "symbol_changed",
    "halted",
    "resumed",
    "delisted",
    "relisted",
)

_ALLOWED: dict[tuple[InstrumentLifecycle, LifecycleEvent], InstrumentLifecycle] = {
    (InstrumentLifecycle.PENDING, "listed"): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, "symbol_changed"): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, "halted"): InstrumentLifecycle.HALTED,
    (InstrumentLifecycle.HALTED, "resumed"): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, "delisted"): InstrumentLifecycle.DELISTED,
    (InstrumentLifecycle.HALTED, "delisted"): InstrumentLifecycle.DELISTED,
}

_AUDIT_TABLE: dict[tuple[InstrumentLifecycle, LifecycleEvent], str] = {
    **{key: AUDIT_EVENT_INSTRUMENT_LISTED for key in [(InstrumentLifecycle.PENDING, "listed")]},
    (InstrumentLifecycle.ACTIVE, "symbol_changed"): AUDIT_EVENT_LISTING_REPLACED,
    (InstrumentLifecycle.ACTIVE, "halted"): AUDIT_EVENT_INSTRUMENT_HALTED,
    (InstrumentLifecycle.HALTED, "resumed"): AUDIT_EVENT_INSTRUMENT_RESUMED,
    (InstrumentLifecycle.ACTIVE, "delisted"): AUDIT_EVENT_INSTRUMENT_DELISTED,
    (InstrumentLifecycle.HALTED, "delisted"): AUDIT_EVENT_INSTRUMENT_DELISTED,
    (InstrumentLifecycle.DELISTED, "relisted"): AUDIT_EVENT_INSTRUMENT_RELISTED,
}

_RELIST_KEY = (InstrumentLifecycle.DELISTED, "relisted")


@pytest.mark.parametrize("state", list(InstrumentLifecycle))
@pytest.mark.parametrize("event", _EVENTS)
def test_transition_matches_table(
    state: InstrumentLifecycle, event: LifecycleEvent
) -> None:
    key = (state, event)
    if key in _ALLOWED:
        assert transition(state, event) == _ALLOWED[key]
    elif key == _RELIST_KEY:
        with pytest.raises(RelistRequiresNewInstrumentError):
            transition(state, event)
    else:
        with pytest.raises(LifecycleTransitionError):
            transition(state, event)


def test_delisted_relisted_does_not_reuse_id_in_place() -> None:
    """§4.2: "delisted+relisted -> 새 instrument 생성(구 id 유지 금지)".

    이 신호는 일반 fail-closed 거부(`LifecycleTransitionError`)와 구별되는
    하위 타입이어야 호출자가 "전이 불가"와 "새 instrument 발급 필요"를
    분기할 수 있다.
    """
    with pytest.raises(RelistRequiresNewInstrumentError):
        transition(InstrumentLifecycle.DELISTED, "relisted")


def test_delisted_rejects_every_other_event() -> None:
    for event in _EVENTS:
        if event == "relisted":
            continue
        with pytest.raises(LifecycleTransitionError):
            transition(InstrumentLifecycle.DELISTED, event)


@pytest.mark.parametrize("state", list(InstrumentLifecycle))
@pytest.mark.parametrize("event", _EVENTS)
def test_audit_event_for_matches_table(
    state: InstrumentLifecycle, event: LifecycleEvent
) -> None:
    key = (state, event)
    if key in _AUDIT_TABLE:
        assert audit_event_for(state, event) == _AUDIT_TABLE[key]
    else:
        with pytest.raises(LifecycleTransitionError):
            audit_event_for(state, event)


def test_audit_event_constants_are_distinct() -> None:
    """§4.2 "감사" 열의 6개 이벤트 이름은 서로 달라야 한다.

    `active+delisted`와 `halted+delisted` 두 행이 같은
    `instrument.delisted`를 공유하는 것은 표대로이므로(단일 이벤트를 두
    출발 state에서 재사용), 이 중복은 여기서 검사하지 않는다.
    """
    names = {
        AUDIT_EVENT_INSTRUMENT_LISTED,
        AUDIT_EVENT_LISTING_REPLACED,
        AUDIT_EVENT_INSTRUMENT_HALTED,
        AUDIT_EVENT_INSTRUMENT_RESUMED,
        AUDIT_EVENT_INSTRUMENT_DELISTED,
        AUDIT_EVENT_INSTRUMENT_RELISTED,
    }
    assert len(names) == 6
