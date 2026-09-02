"""LC-5 — hold_state 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-5
("전이표 전수", negative: "만료된 hold 재사용").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ledger.contracts.v1 import HoldState
from src.foundation.ledger.domain import hold_state as hs

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
_NOT_EXPIRED = _NOW + timedelta(days=1)
_ALREADY_EXPIRED = _NOW - timedelta(days=1)

_ALL_STATES: list[HoldState | None] = [
    None,
    HoldState.PENDING,
    HoldState.CAPTURED,
    HoldState.RELEASED,
    HoldState.EXPIRED,
]
_ALL_EVENTS = list(hs.HoldEvent)

_LEGAL: dict[tuple[HoldState | None, hs.HoldEvent], HoldState] = {
    (None, hs.HoldEvent.PLACE): HoldState.PENDING,
    (HoldState.PENDING, hs.HoldEvent.CAPTURE): HoldState.CAPTURED,
    (HoldState.PENDING, hs.HoldEvent.RELEASE): HoldState.RELEASED,
    (HoldState.PENDING, hs.HoldEvent.EXPIRE): HoldState.EXPIRED,
}


def _expires_at_satisfying_guard(event: hs.HoldEvent) -> datetime:
    """EXPIRE는 now > expires_at을 요구하고 CAPTURE/그 외는 now <= expires_at을
    요구한다 — 전이 합법성만 보고 싶은 테스트가 가드 실패로 흔들리지 않게 한다."""
    return _ALREADY_EXPIRED if event is hs.HoldEvent.EXPIRE else _NOT_EXPIRED


def test_legal_transitions_produce_expected_state() -> None:
    for (frm, event), to in _LEGAL.items():
        got = hs.transition(frm, event, now=_NOW, expires_at=_expires_at_satisfying_guard(event))
        assert got is to, f"{frm} --{event}--> expected {to}, got {got}"


@pytest.mark.parametrize("frm", _ALL_STATES)
@pytest.mark.parametrize("event", _ALL_EVENTS)
def test_transition_table_is_exhaustive(frm: HoldState | None, event: hs.HoldEvent) -> None:
    """허용된 4개 조합 외에는 전부 거부된다(§4.5 전이표 전수)."""
    expires_at = _expires_at_satisfying_guard(event)
    if (frm, event) in _LEGAL:
        hs.transition(frm, event, now=_NOW, expires_at=expires_at)
        return
    with pytest.raises(hs.IllegalHoldTransitionError):
        hs.transition(frm, event, now=_NOW, expires_at=expires_at)


# --- negative ---


def test_capture_after_expiry_rejected() -> None:
    """만료된 hold 재사용: capture 시각이 expires_at을 지났으면 거부."""
    with pytest.raises(hs.HoldExpiredError):
        hs.transition(
            HoldState.PENDING, hs.HoldEvent.CAPTURE, now=_NOW, expires_at=_ALREADY_EXPIRED
        )


def test_expire_before_expiry_time_rejected() -> None:
    with pytest.raises(hs.HoldNotYetExpiredError):
        hs.transition(
            HoldState.PENDING, hs.HoldEvent.EXPIRE, now=_NOW, expires_at=_NOT_EXPIRED
        )


def test_capture_exactly_at_expiry_boundary_is_allowed() -> None:
    """now == expires_at은 아직 만료 전(guard는 now > expires_at일 때만 거부)."""
    got = hs.transition(HoldState.PENDING, hs.HoldEvent.CAPTURE, now=_NOW, expires_at=_NOW)
    assert got is HoldState.CAPTURED


def test_terminal_states_reject_every_event() -> None:
    for frm in (HoldState.CAPTURED, HoldState.RELEASED, HoldState.EXPIRED):
        for event in _ALL_EVENTS:
            with pytest.raises(hs.IllegalHoldTransitionError):
                hs.transition(frm, event, now=_NOW, expires_at=_NOT_EXPIRED)
