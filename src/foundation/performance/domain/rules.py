"""Performance pure rule functions.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.
"""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal


class ScopeMixError(Exception):
    """Error taxonomy #72 `INTEGRITY_PAPER_LIVE_MIX` — a statement attempted to
    mix PAPER and LIVE inputs in a single calculation. In this leaf where
    LIVE data does not yet exist, it can only be reproduced via fixtures (§10 U9)."""

    def __init__(self, scopes: Iterable[str]) -> None:
        distinct = sorted(set(scopes))
        super().__init__(f"INTEGRITY_PAPER_LIVE_MIX: {distinct}")
        self.reason_code = "INTEGRITY_PAPER_LIVE_MIX"
        self.scopes = distinct


class PrecisionError(Exception):
    """Error taxonomy #72 `INTEGRITY_CURRENCY_PRECISION`."""

    def __init__(self, amount: Decimal, expected_exponent: int) -> None:
        super().__init__(
            f"INTEGRITY_CURRENCY_PRECISION: {amount}는 소수 {expected_exponent}자리를 "
            "초과합니다."
        )
        self.reason_code = "INTEGRITY_CURRENCY_PRECISION"


class BenchmarkNotPinnedError(Exception):
    """The benchmark is pinned to the mandate value at period start — even if the
    mandate changes during the period, already-computed statements are not
    retroactively modified."""


def assert_single_scope(scopes: Iterable[str]) -> None:
    distinct = set(scopes)
    if len(distinct) > 1:
        raise ScopeMixError(distinct)


def assert_precision(amount: Decimal, *, expected_exponent: int) -> None:
    if not amount.is_finite():
        raise PrecisionError(amount, expected_exponent)
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > expected_exponent:
        raise PrecisionError(amount, expected_exponent)


def assert_benchmark_pinned(
    *, benchmark_ref: str | None, pinned_at_period_start: str | None
) -> None:
    if benchmark_ref != pinned_at_period_start:
        raise BenchmarkNotPinnedError(
            f"benchmark_ref={benchmark_ref!r}가 기간 시작 고정값 "
            f"{pinned_at_period_start!r}과 다릅니다."
        )


def next_revision(prev: int) -> int:
    return prev + 1
