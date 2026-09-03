"""DC-3 — 심볼 생애주기 전이표(순수 상태기계).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§4.2(전이표), §9.2 DC-3.

`domain/reference/lifecycle.py`(LA-7)는 LA-1 계약(contracts/v1)의
`SymbolStatus`(PENDING/LISTED/SUSPENDED/DELISTED)와 이벤트
`LIST/SUSPEND/RESUME/DELIST/RENAME`을 쓰는 별도 상태기계다. 이 모듈은
DC-1 계약(contracts/v2)의 `InstrumentLifecycle`(PENDING/ACTIVE/HALTED/
DELISTED)과 §4.2 표의 이벤트(listed/symbol_changed/halted/resumed/
delisted/relisted)를 쓴다 — 어휘·행 구성이 달라 동형이 아니므로 통합하지
않는다(task-1125 decision).

§4.2 표의 가드(벤처 확인, 새 listing 등록)는 리포지토리 조회가 필요해
순수 함수 범위 밖이다. 이 함수는 상태×이벤트만으로 정해지는 전이 가능
여부와, 그 전이에 대응하는 감사 이벤트 이름만 판정한다.
"""
from __future__ import annotations

from typing import Final, Literal

from src.foundation.market_data.contracts.v2.instruments import InstrumentLifecycle

__all__ = [
    "LifecycleEvent",
    "LifecycleTransitionError",
    "RelistRequiresNewInstrumentError",
    "EVENT_LISTED",
    "EVENT_SYMBOL_CHANGED",
    "EVENT_HALTED",
    "EVENT_RESUMED",
    "EVENT_DELISTED",
    "EVENT_RELISTED",
    "AUDIT_EVENT_INSTRUMENT_LISTED",
    "AUDIT_EVENT_LISTING_REPLACED",
    "AUDIT_EVENT_INSTRUMENT_HALTED",
    "AUDIT_EVENT_INSTRUMENT_RESUMED",
    "AUDIT_EVENT_INSTRUMENT_DELISTED",
    "AUDIT_EVENT_INSTRUMENT_RELISTED",
    "transition",
    "audit_event_for",
]

LifecycleEvent = Literal[
    "listed", "symbol_changed", "halted", "resumed", "delisted", "relisted"
]

EVENT_LISTED: Final[LifecycleEvent] = "listed"
EVENT_SYMBOL_CHANGED: Final[LifecycleEvent] = "symbol_changed"
EVENT_HALTED: Final[LifecycleEvent] = "halted"
EVENT_RESUMED: Final[LifecycleEvent] = "resumed"
EVENT_DELISTED: Final[LifecycleEvent] = "delisted"
EVENT_RELISTED: Final[LifecycleEvent] = "relisted"

# §4.2 "감사" 열 — 단일출처(SSOT). 호출자(application 계층)는 이 문자열을
# 직접 다시 쓰지 않고 이 상수(또는 `audit_event_for`)만 참조한다.
AUDIT_EVENT_INSTRUMENT_LISTED: Final[str] = "instrument.listed"
AUDIT_EVENT_LISTING_REPLACED: Final[str] = "listing.replaced"
AUDIT_EVENT_INSTRUMENT_HALTED: Final[str] = "instrument.halted"
AUDIT_EVENT_INSTRUMENT_RESUMED: Final[str] = "instrument.resumed"
AUDIT_EVENT_INSTRUMENT_DELISTED: Final[str] = "instrument.delisted"
AUDIT_EVENT_INSTRUMENT_RELISTED: Final[str] = "instrument.relisted"

# §4.2 표 6행. (state, event) -> 다음 state. delisted+relisted는 같은
# instrument_id의 in-place 전이가 아니므로(새 instrument 발급) 여기 없다
# — `transition`이 별도로 `RelistRequiresNewInstrumentError`를 던진다.
_TRANSITIONS: Final[dict[tuple[InstrumentLifecycle, LifecycleEvent], InstrumentLifecycle]] = {
    (InstrumentLifecycle.PENDING, EVENT_LISTED): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, EVENT_SYMBOL_CHANGED): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, EVENT_HALTED): InstrumentLifecycle.HALTED,
    (InstrumentLifecycle.HALTED, EVENT_RESUMED): InstrumentLifecycle.ACTIVE,
    (InstrumentLifecycle.ACTIVE, EVENT_DELISTED): InstrumentLifecycle.DELISTED,
    (InstrumentLifecycle.HALTED, EVENT_DELISTED): InstrumentLifecycle.DELISTED,
}

# §4.2 표 6행 전체(relisted 포함)에 대한 감사 이벤트 이름.
_AUDIT_EVENTS: Final[dict[tuple[InstrumentLifecycle, LifecycleEvent], str]] = {
    (InstrumentLifecycle.PENDING, EVENT_LISTED): AUDIT_EVENT_INSTRUMENT_LISTED,
    (InstrumentLifecycle.ACTIVE, EVENT_SYMBOL_CHANGED): AUDIT_EVENT_LISTING_REPLACED,
    (InstrumentLifecycle.ACTIVE, EVENT_HALTED): AUDIT_EVENT_INSTRUMENT_HALTED,
    (InstrumentLifecycle.HALTED, EVENT_RESUMED): AUDIT_EVENT_INSTRUMENT_RESUMED,
    (InstrumentLifecycle.ACTIVE, EVENT_DELISTED): AUDIT_EVENT_INSTRUMENT_DELISTED,
    (InstrumentLifecycle.HALTED, EVENT_DELISTED): AUDIT_EVENT_INSTRUMENT_DELISTED,
    (InstrumentLifecycle.DELISTED, EVENT_RELISTED): AUDIT_EVENT_INSTRUMENT_RELISTED,
}


class LifecycleTransitionError(ValueError):
    """§4.2 표에 없는 (state, event) 조합 — fail-closed 거부."""


class RelistRequiresNewInstrumentError(LifecycleTransitionError):
    """(DELISTED, relisted)는 같은 instrument_id의 in-place 전이가 아니다.

    §4.2: "delisted+relisted -> 새 instrument 생성(구 id 유지 금지)". 호출자는
    이 예외를 받으면 기존 instrument를 갱신하지 말고 새 `Instrument`(새
    ULID)를 발급해야 한다.
    """


def transition(
    state: InstrumentLifecycle, event: LifecycleEvent
) -> InstrumentLifecycle:
    """§4.2 상태기계.

    delisted+relisted는 새 instrument 발급을 요구하므로 in-place 전이가
    아니다 — `RelistRequiresNewInstrumentError`로 신호한다. 표에 없는
    나머지 (state, event) 조합은 전부 `LifecycleTransitionError`
    (fail-closed).
    """
    if state is InstrumentLifecycle.DELISTED and event == EVENT_RELISTED:
        raise RelistRequiresNewInstrumentError(
            "delisted -> relisted는 새 instrument 발급이 필요하다(구 id 유지 금지)"
        )
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError as exc:
        raise LifecycleTransitionError(
            f"허용되지 않는 전이: {state.value} + {event}"
        ) from exc


def audit_event_for(state: InstrumentLifecycle, event: LifecycleEvent) -> str:
    """(state, event)에 대응하는 §4.2 감사 이벤트 이름. 표에 없으면
    `LifecycleTransitionError`(fail-closed) — `transition`과 동일한 판정
    기준을 쓴다."""
    try:
        return _AUDIT_EVENTS[(state, event)]
    except KeyError as exc:
        raise LifecycleTransitionError(
            f"허용되지 않는 전이: {state.value} + {event}"
        ) from exc
