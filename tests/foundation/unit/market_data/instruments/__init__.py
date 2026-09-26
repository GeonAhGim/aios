"""task-8401: DC-3 fail-closed regression checks (INVARIANTS I-07/I-10).

Explicitly runnable package entry point required by the assigned DEEPEN task.
These checks exercise production lifecycle rules, not package-local validators.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.foundation.market_data.contracts.v2.instruments import InstrumentLifecycle as State
from src.foundation.market_data.domain.instruments import lifecycle


@pytest.mark.parametrize("event", [None, "", "LISTED", "listed\x00", ("listed",)])
def test_negative_malformed_event_is_rejected(event: object) -> None:
    """I-07: malformed external events cannot activate an instrument."""
    with pytest.raises(lifecycle.LifecycleTransitionError, match="Transition not allowed"):
        lifecycle.transition(State.PENDING, event)
    with pytest.raises(lifecycle.LifecycleTransitionError, match="Transition not allowed"):
        lifecycle.audit_event_for(State.PENDING, event)


@pytest.mark.parametrize("event", ["listed", "resumed", "symbol_changed"])
def test_negative_adversarial_delisted_cannot_be_reactivated(event: str) -> None:
    """DC-3: a stale event cannot resurrect the old instrument identity."""
    with pytest.raises(lifecycle.LifecycleTransitionError, match="Transition not allowed"):
        lifecycle.transition(State.DELISTED, event)
    with pytest.raises(lifecycle.LifecycleTransitionError, match="Transition not allowed"):
        lifecycle.audit_event_for(State.DELISTED, event)


def test_negative_relisting_requires_new_identity() -> None:
    with pytest.raises(lifecycle.RelistRequiresNewInstrumentError, match="new instrument"):
        lifecycle.transition(State.DELISTED, "relisted")


@pytest.mark.parametrize(
    ("table", "operation", "expected"),
    [
        ("_TRANSITIONS", lifecycle.transition, State.ACTIVE),
        ("_AUDIT_EVENTS", lifecycle.audit_event_for, "instrument.listed"),
    ],
)
def test_failure_injection_lookup_error_is_closed_and_recovers(
    monkeypatch: pytest.MonkeyPatch, table: str, operation: object, expected: object,
) -> None:
    """I-10: dependency failure must reach callers, never fabricate success."""
    lookup = MagicMock()
    failure = KeyError("injected lookup failure")
    lookup.__getitem__.side_effect = failure
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, table, lookup)
        with pytest.raises(lifecycle.LifecycleTransitionError) as caught:
            operation(State.PENDING, "listed")
        assert caught.value.__cause__ is failure
        lookup.__getitem__.assert_called_once_with((State.PENDING, "listed"))
    assert operation(State.PENDING, "listed") == expected


def test_replay_verify_lifecycle_trace() -> None:
    """Repeated replay preserves both state decisions and audit event ordering."""
    events = ("listed", "halted", "resumed", "symbol_changed", "delisted")
    expected = [
        (State.ACTIVE, "instrument.listed"),
        (State.HALTED, "instrument.halted"),
        (State.ACTIVE, "instrument.resumed"),
        (State.ACTIVE, "listing.replaced"),
        (State.DELISTED, "instrument.delisted"),
    ]
    for _ in range(3):
        state = State.PENDING
        trace = []
        for event in events:
            audit = lifecycle.audit_event_for(state, event)
            state = lifecycle.transition(state, event)
            trace.append((state, audit))
        assert trace == expected
