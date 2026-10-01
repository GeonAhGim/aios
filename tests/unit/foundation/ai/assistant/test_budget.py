"""U-3a -- domain/budget.py 순수 함수 단위 테스트."""

from __future__ import annotations

import pytest

from src.foundation.ai.assistant.domain.budget import (
    BudgetExceededError,
    check_daily_budget,
    enforce_daily_budget,
)


def test_under_cap_is_allowed() -> None:
    decision = check_daily_budget(used_today=3, daily_cap=50)
    assert decision.allowed is True
    assert decision.remaining == 47


def test_at_cap_is_denied() -> None:
    decision = check_daily_budget(used_today=50, daily_cap=50)
    assert decision.allowed is False
    assert decision.remaining == 0


def test_zero_cap_is_fail_closed() -> None:
    decision = check_daily_budget(used_today=0, daily_cap=0)
    assert decision.allowed is False


def test_enforce_raises_with_used_and_cap() -> None:
    with pytest.raises(BudgetExceededError) as exc_info:
        enforce_daily_budget(used_today=10, daily_cap=10)
    assert exc_info.value.used == 10
    assert exc_info.value.cap == 10


def test_enforce_passes_through_decision_when_allowed() -> None:
    decision = enforce_daily_budget(used_today=0, daily_cap=1)
    assert decision.allowed is True


def test_negative_daily_cap_is_fail_closed() -> None:
    """A negative cap (misconfiguration) must never be read as unlimited."""
    decision = check_daily_budget(used_today=0, daily_cap=-5)
    assert decision.allowed is False
    assert decision.remaining == 0


def test_used_above_cap_is_denied_with_zero_remaining() -> None:
    """Usage already past the cap (e.g. cap lowered mid-day) must deny, not
    report a negative `remaining`."""
    decision = check_daily_budget(used_today=60, daily_cap=50)
    assert decision.allowed is False
    assert decision.remaining == 0


def test_enforce_raises_for_negative_cap() -> None:
    with pytest.raises(BudgetExceededError) as exc_info:
        enforce_daily_budget(used_today=0, daily_cap=-1)
    assert exc_info.value.used == 0
    assert exc_info.value.cap == -1


def test_enforce_propagates_decision_construction_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: if the underlying decision object cannot be built
    (e.g. a future field-validation change in `BudgetDecision`), the
    judgment must surface that failure rather than silently allowing the
    request through."""
    import src.foundation.ai.assistant.domain.budget as budget_module

    def _broken_decision(*args: object, **kwargs: object) -> None:
        raise RuntimeError("decision construction failed")

    monkeypatch.setattr(budget_module, "BudgetDecision", _broken_decision)

    with pytest.raises(RuntimeError, match="decision construction failed"):
        enforce_daily_budget(used_today=0, daily_cap=10)
