"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 95, §9 L18 — aggregation.py tests.

DoD (a)-(e) must all be falsifiable: dropping the exact contract, breaking
the sum identity, defaulting `as_of`, or letting `total_equity<=0` reach a
`ZeroDivisionError` must each fail some test here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import cast

import pytest
from pydantic import ValidationError

from src.core.portfolio.aggregation import (
    ExecutionExposure,
    MissingAsOfError,
    NonPositiveEquityError,
    aggregate,
)
from tests.conftest import PerfBudget

_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _exposures() -> list[ExecutionExposure]:
    return [
        ExecutionExposure(
            execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("2000")
        ),
        ExecutionExposure(
            execution_id=2, strategy_id="strat-a", symbol="ETH/USDT", notional=Decimal("1000")
        ),
        ExecutionExposure(
            execution_id=3,
            strategy_id="strat-b",
            symbol="BTC/USDT",
            notional=Decimal("2000"),
            vol_pct=Decimal("15"),
        ),
    ]


# --- (a) exact public contract ------------------------------------------------


def test_execution_exposure_has_exactly_the_spec_fields():
    expected = {"execution_id", "strategy_id", "symbol", "notional", "vol_pct"}
    assert set(ExecutionExposure.model_fields.keys()) == expected


def test_execution_exposure_vol_pct_defaults_to_none():
    exposure = ExecutionExposure(
        execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("100")
    )
    assert exposure.vol_pct is None


# --- (b) sum identities, exact Decimal ----------------------------------------


def test_sum_identity_symbol_strategy_and_total_match_exactly():
    agg = aggregate(_exposures(), cash=Decimal("5000"), as_of=_AS_OF)

    assert sum(agg.per_symbol_pct.values(), Decimal("0")) == agg.total_exposure_pct
    assert sum(agg.per_strategy_pct.values(), Decimal("0")) == agg.total_exposure_pct
    assert agg.cash_pct + agg.total_exposure_pct == Decimal("100")


def test_aggregate_values_are_exact_decimals():
    # total_equity = 5000 cash + 5000 notional = 10000
    agg = aggregate(_exposures(), cash=Decimal("5000"), as_of=_AS_OF)

    assert agg.total_equity == Decimal("10000")
    assert agg.per_symbol_pct["BTC/USDT"] == Decimal("40")  # 4000/10000*100
    assert agg.per_symbol_pct["ETH/USDT"] == Decimal("10")  # 1000/10000*100
    assert agg.per_strategy_pct["strat-a"] == Decimal("30")  # 3000/10000*100
    assert agg.per_strategy_pct["strat-b"] == Decimal("20")  # 2000/10000*100
    assert agg.total_exposure_pct == Decimal("50")
    assert agg.cash_pct == Decimal("50")
    assert agg.as_of == _AS_OF


def test_aggregate_with_no_exposures_is_all_cash():
    agg = aggregate([], cash=Decimal("1000"), as_of=_AS_OF)

    assert agg.total_equity == Decimal("1000")
    assert agg.per_symbol_pct == {}
    assert agg.per_strategy_pct == {}
    assert agg.total_exposure_pct == Decimal("0")
    assert agg.cash_pct == Decimal("100")


# --- (c) as_of is mandatory, never defaulted ----------------------------------


def test_aggregate_rejects_as_of_none_instead_of_defaulting():
    with pytest.raises(MissingAsOfError):
        aggregate(_exposures(), cash=Decimal("5000"), as_of=cast(datetime, None))


# --- (d) total_equity<=0 is a defined rejection, not ZeroDivisionError -------


def test_aggregate_rejects_zero_total_equity():
    with pytest.raises(NonPositiveEquityError):
        aggregate([], cash=Decimal("0"), as_of=_AS_OF)


def test_aggregate_rejects_negative_total_equity():
    exposures = [
        ExecutionExposure(
            execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("-500")
        )
    ]
    with pytest.raises(NonPositiveEquityError):
        aggregate(exposures, cash=Decimal("100"), as_of=_AS_OF)


# --- Decimal-only enforcement (105 standard) ----------------------------------


def test_execution_exposure_rejects_float_notional():
    with pytest.raises(ValidationError):
        ExecutionExposure(execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=1.5)


def test_execution_exposure_rejects_float_vol_pct():
    with pytest.raises(ValidationError):
        ExecutionExposure(
            execution_id=1,
            strategy_id="strat-a",
            symbol="BTC/USDT",
            notional=Decimal("100"),
            vol_pct=15.0,
        )


# --- D2 실패 주입 (failure injection) -----------------------------------------


class _BrokenExposureFeed:
    """Stand-in for an upstream execution-store feed (e.g. a reconciliation
    snapshot mid-fetch) whose iteration breaks partway through instead of
    yielding a clean, complete sequence."""

    def __iter__(self):
        yield ExecutionExposure(
            execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("1000")
        )
        raise RuntimeError("upstream execution feed disconnected mid-read")


def test_aggregate_propagates_broken_exposure_feed_failure():
    """`aggregate()` must let a broken upstream exposures feed's exception
    propagate (fail-closed) instead of silently returning a partial
    aggregate that a downstream risk gate would then treat as complete."""
    with pytest.raises(RuntimeError, match="disconnected mid-read"):
        aggregate(cast(list, _BrokenExposureFeed()), cash=Decimal("5000"), as_of=_AS_OF)


# --- D2 성능 단언 (performance assertion) --------------------------------------


@pytest.mark.perf
def test_aggregate_p99_latency_within_pretrade_gate_budget(perf_budget: PerfBudget):
    """ADR-2026-09-09-C Decision 1 축별 성능 예산: 사전거래 게이트 p99 5ms.
    `aggregate()`는 L17 `PortfolioStateInput.exposures` 조립 경로에서 사이징
    직전에 호출된다(모듈 docstring). task-7434: process_time 기반
    perf_budget으로 측정해 xdist 코어 경합 노이즈를 배제한다."""
    exposures = [
        ExecutionExposure(
            execution_id=i,
            strategy_id=f"strat-{i % 5}",
            symbol=f"SYM{i % 20}/USDT",
            notional=Decimal("100"),
        )
        for i in range(500)
    ]

    samples = perf_budget.samples(
        lambda: aggregate(exposures, cash=Decimal("5000"), as_of=_AS_OF), n=200
    )
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_values_ms[int(len(cpu_values_ms) * 0.99)]
    assert p99_ms < 5.0, f"p99={p99_ms:.3f}ms exceeds 5ms budget"


# --- D2 게이트 적색 재현 (gate-red reproduction) --------------------------------


def _regressed_pct_without_equity_guard(
    exposures: list[ExecutionExposure], cash: Decimal
) -> Decimal:
    """실제 `aggregate()`의 `total_equity <= 0` 가드(`NonPositiveEquityError`)를
    빼먹은 회귀본 — 퍼센트 계산이 그대로 진행돼 정의되지 않은 나눗셈 예외로
    새어나간다."""
    total_notional = sum((exposure.notional for exposure in exposures), Decimal("0"))
    total_equity = cash + total_notional
    return total_notional / total_equity * Decimal("100")


def test_missing_equity_guard_would_leak_undefined_division_error():
    """가드가 빠진 회귀본은 총자산 0에서 `decimal.InvalidOperation`(0/0 미정의)을
    그대로 새어나가게 한다(적색) — 실제 구현은 같은 입력을 정의된
    `NonPositiveEquityError`로 fail-closed 거부해야 한다(녹색)."""
    # 적색: 가드가 없으면 정의되지 않은 예외가 새어나간다.
    with pytest.raises(InvalidOperation):
        _regressed_pct_without_equity_guard([], cash=Decimal("0"))

    # 녹색: 실제 구현은 같은 입력을 정의된 예외로 거부한다.
    with pytest.raises(NonPositiveEquityError):
        aggregate([], cash=Decimal("0"), as_of=_AS_OF)
