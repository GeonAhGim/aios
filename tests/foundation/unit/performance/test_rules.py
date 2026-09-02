"""Performance 순수 규칙 — PAPER/LIVE 혼합 거부, 정밀도 위반.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.performance.domain.rules import (
    BenchmarkNotPinnedError,
    PrecisionError,
    ScopeMixError,
    assert_benchmark_pinned,
    assert_precision,
    assert_single_scope,
    next_revision,
)


def test_assert_single_scope_allows_uniform_scope():
    assert_single_scope(["PAPER", "PAPER", "PAPER"])  # 예외 없이 통과


def test_assert_single_scope_rejects_paper_live_mix():
    with pytest.raises(ScopeMixError) as excinfo:
        assert_single_scope(["PAPER", "LIVE"])
    assert excinfo.value.reason_code == "INTEGRITY_PAPER_LIVE_MIX"


def test_assert_precision_allows_exact_or_fewer_decimals():
    assert_precision(Decimal("100.12"), expected_exponent=2)
    assert_precision(Decimal("100"), expected_exponent=2)


def test_assert_precision_rejects_excess_decimals():
    with pytest.raises(PrecisionError) as excinfo:
        assert_precision(Decimal("100.12345"), expected_exponent=2)
    assert excinfo.value.reason_code == "INTEGRITY_CURRENCY_PRECISION"


def test_assert_benchmark_pinned_rejects_drift():
    with pytest.raises(BenchmarkNotPinnedError):
        assert_benchmark_pinned(benchmark_ref="BTCUSDT", pinned_at_period_start="ETHUSDT")


def test_assert_benchmark_pinned_allows_match():
    assert_benchmark_pinned(benchmark_ref="BTCUSDT", pinned_at_period_start="BTCUSDT")


def test_next_revision_increments():
    assert next_revision(1) == 2
    assert next_revision(5) == 6
