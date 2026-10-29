"""QA negative tests: the deny list must not be mutable at runtime (fail-open guard)."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.logging.redaction import DENY_KEYS, REDACTED, redact

# Untyped alias so the mutation attempts reach runtime instead of being rejected by mypy.
_untyped_deny: Any = DENY_KEYS


def test_deny_list_is_immutable() -> None:
    """A mutable deny list lets a caller clear masking process-wide (regression for c54ff2ac)."""
    assert isinstance(DENY_KEYS, frozenset)
    assert not hasattr(DENY_KEYS, "clear")
    assert not hasattr(DENY_KEYS, "discard")


@pytest.mark.parametrize(
    ("mutate", "args"),
    [("clear", ()), ("discard", ("password",)), ("add", ("x",))],
)
def test_mutating_deny_list_is_rejected(mutate: str, args: tuple[str, ...]) -> None:
    """Any attempt to mutate the deny list raises instead of disabling redaction."""
    with pytest.raises(AttributeError):
        getattr(_untyped_deny, mutate)(*args)


def test_password_stays_masked_after_failed_mutation() -> None:
    """After a rejected mutation, the password key is still masked (fail-closed)."""
    with pytest.raises(AttributeError):
        _untyped_deny.clear()

    assert redact({"password": "hunter2"}) == {"password": REDACTED}
