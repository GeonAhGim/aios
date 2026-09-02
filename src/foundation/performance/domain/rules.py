"""Performance 순수 규칙 함수.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.
"""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal


class ScopeMixError(Exception):
    """72번 에러 taxonomy `INTEGRITY_PAPER_LIVE_MIX` — 하나의 statement가
    PAPER와 LIVE 입력을 섞어 계산하려 했다. LIVE 데이터가 아직 없는
    이 리프에서는 픽스처로만 재현 가능하다(§10 U9)."""

    def __init__(self, scopes: Iterable[str]) -> None:
        distinct = sorted(set(scopes))
        super().__init__(f"INTEGRITY_PAPER_LIVE_MIX: {distinct}")
        self.reason_code = "INTEGRITY_PAPER_LIVE_MIX"
        self.scopes = distinct


class PrecisionError(Exception):
    """72번 에러 taxonomy `INTEGRITY_CURRENCY_PRECISION`."""

    def __init__(self, amount: Decimal, expected_exponent: int) -> None:
        super().__init__(
            f"INTEGRITY_CURRENCY_PRECISION: {amount}는 소수 {expected_exponent}자리를 "
            "초과합니다."
        )
        self.reason_code = "INTEGRITY_CURRENCY_PRECISION"


class BenchmarkNotPinnedError(Exception):
    """벤치마크는 기간 시작 시점 mandate 지정값에 고정된다 — 기간 중 mandate가
    바뀌어도 이미 계산된 statement는 소급 변경되지 않는다."""


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
