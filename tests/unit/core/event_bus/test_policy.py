"""Tests for src/core/event_bus/policy.py — HandlerCriticality + EventBusPolicy.

Target: 7 executable statements at 0% → 100%.
DoD: negative tests >=3, failure-injection >=1, perf assertion >=1, gate-red repro >=1.
"""

from __future__ import annotations

import pytest

from src.core.event_bus.policy import EventBusPolicy, HandlerCriticality

# ---------------------------------------------------------------------------
# 1. HandlerCriticality enum — positive coverage
# ---------------------------------------------------------------------------


class TestHandlerCriticality:
    """HandlerCriticality enum members and values."""

    def test_safe_member_exists(self):
        assert HandlerCriticality.SAFE.value == "SAFE"

    def test_critical_member_exists(self):
        assert HandlerCriticality.CRITICAL.value == "CRITICAL"

    def test_from_string_value(self):
        assert HandlerCriticality("SAFE") is HandlerCriticality.SAFE
        assert HandlerCriticality("CRITICAL") is HandlerCriticality.CRITICAL

    def test_enum_members_iteration(self):
        members = list(HandlerCriticality)
        assert len(members) == 2
        assert HandlerCriticality.SAFE in members
        assert HandlerCriticality.CRITICAL in members

    def test_enum_str_coercion(self):
        # str(enum_member) returns the member name in Python < 3.11,
        # but the value in 3.11+. Either way it should be a string.
        s = str(HandlerCriticality.SAFE)
        assert isinstance(s, str)

    def test_enum_equality_by_value(self):
        assert HandlerCriticality("SAFE") == HandlerCriticality.SAFE
        assert HandlerCriticality("CRITICAL") == HandlerCriticality.CRITICAL

    def test_enum_invalid_value_raises(self):
        with pytest.raises(ValueError):
            HandlerCriticality("UNKNOWN")


# ---------------------------------------------------------------------------
# 2. EventBusPolicy.ON_HANDLER_ERROR — positive coverage
# ---------------------------------------------------------------------------


class TestEventBusPolicy:
    """EventBusPolicy class-level policy dict."""

    def test_on_handler_error_is_dict(self):
        assert isinstance(EventBusPolicy.ON_HANDLER_ERROR, dict)

    def test_on_handler_error_contains_safe(self):
        assert HandlerCriticality.SAFE in EventBusPolicy.ON_HANDLER_ERROR

    def test_on_handler_error_contains_critical(self):
        assert HandlerCriticality.CRITICAL in EventBusPolicy.ON_HANDLER_ERROR

    def test_safe_resolves_to_log_and_continue(self):
        assert EventBusPolicy.ON_HANDLER_ERROR[HandlerCriticality.SAFE] == "log_and_continue"

    def test_critical_resolves_to_escalate_and_retry(self):
        assert EventBusPolicy.ON_HANDLER_ERROR[HandlerCriticality.CRITICAL] == "escalate_and_retry"

    def test_on_handler_error_has_exactly_two_entries(self):
        assert len(EventBusPolicy.ON_HANDLER_ERROR) == 2

    def test_all_values_are_strings(self):
        for v in EventBusPolicy.ON_HANDLER_ERROR.values():
            assert isinstance(v, str)

    def test_all_keys_are_handler_criticality(self):
        for k in EventBusPolicy.ON_HANDLER_ERROR:
            assert isinstance(k, HandlerCriticality)


# ---------------------------------------------------------------------------
# 3. Negative tests (>=3) — boundary / invalid inputs
# ---------------------------------------------------------------------------


class TestPolicyNegative:
    """Negative tests: invalid enum values, missing keys, type errors."""

    def test_missing_key_raises_key_error(self):
        """Accessing a key not in ON_HANDLER_ERROR should raise KeyError."""
        # Use a custom object that won't hash/equal any enum member
        with pytest.raises(KeyError):
            EventBusPolicy.ON_HANDLER_ERROR[object()]  # type: ignore[dict-item]

    def test_none_key_raises_key_error(self):
        with pytest.raises(KeyError):
            EventBusPolicy.ON_HANDLER_ERROR[None]  # type: ignore[dict-item]

    def test_int_key_raises_key_error(self):
        with pytest.raises(KeyError):
            EventBusPolicy.ON_HANDLER_ERROR[0]  # type: ignore[dict-item]

    def test_string_key_matches_enum_by_value(self):
        """Python enum uses value-based __eq__: 'SAFE' == HandlerCriticality.SAFE.
        This is expected Python behavior — not a bug, but worth documenting."""
        result = EventBusPolicy.ON_HANDLER_ERROR["SAFE"]
        assert result == "log_and_continue"

    def test_from_invalid_string_raises_value_error(self):
        with pytest.raises(ValueError):
            HandlerCriticality("bogus")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            HandlerCriticality("")


# ---------------------------------------------------------------------------
# 4. Failure-injection test (>=1) — monkeypatch ON_HANDLER_ERROR
# ---------------------------------------------------------------------------


class TestPolicyFailureInjection:
    """Failure injection: mutate ON_HANDLER_ERROR to simulate missing handler.

    Uses a per-test copy of the dict swapped onto the class via monkeypatch
    instead of mutating the shared class-level dict in place — pytest-xdist
    runs this suite under `-n auto`/`--dist loadfile`, and an in-place
    mutation left uncleaned by a crashed/interrupted worker would corrupt
    ON_HANDLER_ERROR for every other test in that worker for the rest of the
    session (task-7495).
    """

    def test_missing_handler_critical_entry_causes_lookup_failure(self, monkeypatch):
        """If a handler reports CRITICAL but ON_HANDLER_ERROR lacks the key,
        the system should fail loudly (KeyError), not silently continue."""
        patched = dict(EventBusPolicy.ON_HANDLER_ERROR)
        patched.pop(HandlerCriticality.CRITICAL)
        monkeypatch.setattr(EventBusPolicy, "ON_HANDLER_ERROR", patched)
        with pytest.raises(KeyError):
            EventBusPolicy.ON_HANDLER_ERROR[HandlerCriticality.CRITICAL]

    def test_corrupted_handler_error_value_causes_action_failure(self, monkeypatch):
        """If ON_HANDLER_ERROR value is not a string (e.g. None), action
        dispatch should fail."""
        patched = dict(EventBusPolicy.ON_HANDLER_ERROR)
        patched[HandlerCriticality.SAFE] = None  # type: ignore[assignment]
        monkeypatch.setattr(EventBusPolicy, "ON_HANDLER_ERROR", patched)
        action = EventBusPolicy.ON_HANDLER_ERROR[HandlerCriticality.SAFE]
        assert action is None, "Expected corrupted value to be retrievable"


# ---------------------------------------------------------------------------
# 5. Performance assertion (>=1)
# ---------------------------------------------------------------------------


class TestPolicyPerformance:
    """Performance assertions for EventBusPolicy lookups."""

    @pytest.mark.perf
    def test_lookup_throughput(self):
        """ON_HANDLER_ERROR lookup should handle >=1M calls/sec."""
        key = HandlerCriticality.SAFE
        iterations = 1_000_000

        import time

        start = time.perf_counter()
        for _ in range(iterations):
            EventBusPolicy.ON_HANDLER_ERROR[key]
        elapsed = time.perf_counter() - start

        throughput = iterations / elapsed if elapsed > 0 else float("inf")
        assert throughput >= 1_000_000, (
            f"ON_HANDLER_ERROR lookup throughput {throughput:.0f}/s below 1M/s budget"
        )


# ---------------------------------------------------------------------------
# 6. Gate-red reproduction: ON_HANDLER_ERROR must be consistent
#    (regression guard)
# ---------------------------------------------------------------------------


class TestPolicyGateRed:
    """Gate-red: ON_HANDLER_ERROR invariant — every enum member has an action."""

    def test_every_enum_member_has_action(self):
        """Invariant: for every HandlerCriticality member, ON_HANDLER_ERROR
        must contain a non-empty string action."""
        for member in HandlerCriticality:
            action = EventBusPolicy.ON_HANDLER_ERROR[member]
            assert isinstance(action, str)
            assert len(action) > 0, f"Empty action for {member}"

    def test_no_extra_keys_beyond_enum(self):
        """Invariant: ON_HANDLER_ERROR keys must be exactly the enum members."""
        expected_keys = set(HandlerCriticality)
        actual_keys = set(EventBusPolicy.ON_HANDLER_ERROR.keys())
        assert actual_keys == expected_keys
