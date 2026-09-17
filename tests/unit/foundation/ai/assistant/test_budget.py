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
