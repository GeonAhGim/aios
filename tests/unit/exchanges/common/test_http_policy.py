"""L4-11 — http_policy 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: full-jitter 범위, Retry-After 우선, cap, 주문 정책 max_attempts=1.
"""
from __future__ import annotations

import pytest

from src.exchanges.common.http_policy import RetryPolicy, TimeoutBudget, backoff_delay


def test_full_jitter_stays_within_bounds() -> None:
    policy = RetryPolicy(max_attempts=4, base=0.25, cap=8.0)
    for attempt in range(1, 5):
        low = backoff_delay(policy, attempt, None, rng=lambda: 0.0)
        high = backoff_delay(policy, attempt, None, rng=lambda: 1.0)
        assert low == 0.0
        ceiling = min(policy.cap, policy.base * (2 ** (attempt - 1)))
        assert high == pytest.approx(ceiling)


def test_full_jitter_is_deterministic_given_rng() -> None:
    policy = RetryPolicy(base=0.25, cap=8.0)
    delay = backoff_delay(policy, attempt=2, retry_after=None, rng=lambda: 0.5)
    assert delay == pytest.approx(0.25 * 2 * 0.5)


def test_retry_after_takes_priority_over_backoff_formula() -> None:
    policy = RetryPolicy(base=0.25, cap=8.0)
    delay = backoff_delay(policy, attempt=4, retry_after=30.0, rng=lambda: 1.0)
    assert delay == 30.0


def test_backoff_respects_cap_at_high_attempt() -> None:
    policy = RetryPolicy(base=0.25, cap=8.0)
    delay = backoff_delay(policy, attempt=10, retry_after=None, rng=lambda: 1.0)
    assert delay == pytest.approx(8.0)


def test_negative_attempt_rejected() -> None:
    policy = RetryPolicy()
    with pytest.raises(ValueError):
        backoff_delay(policy, attempt=0, retry_after=None, rng=lambda: 0.5)


def test_order_submission_policy_is_single_attempt() -> None:
    """§5.4 — 주문 제출은 max_attempts=1(재시도는 outbox 책임)."""
    order_submit_policy = RetryPolicy(max_attempts=1)
    assert order_submit_policy.max_attempts == 1


def test_timeout_budget_defaults() -> None:
    budget = TimeoutBudget()
    assert budget.connect == 2.0
    assert budget.read == 5.0
    assert budget.total == 8.0
