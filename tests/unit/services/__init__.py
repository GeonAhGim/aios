"""Negative and failure-injection tests for src/services/ modules.

Covers: ConditionCompiler, PreviewCalculator, CapitalAllocation,
ConditionEvaluation.

Spec: task-10182 DEEPEN — negative test >= 3, failure injection >= 1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.indicators.talib_adapter import IndicatorService
from src.core.loader.risk_policy_loader import StrategyAllocationPolicy
from src.data.models.market_data import Candle
from src.services.capital_allocation import (
    CapitalAllocationError,
    allocation_cap_pct,
    validate_capital_allocation,
)
from src.services.condition_compiler import (
    ConditionCompileError,
    ConditionCompiler,
)
from src.services.condition_evaluation import compare_value
from src.services.preview_service import PreviewCalculator, PreviewCondition

# ── Helpers ───────────────────────────────────────────────────────────

_SAMPLE_CANDLE = Candle(
    symbol="BTCUSDT",
    exchange="bitget",
    timeframe="1m",
    open_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
    close_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
    open=Decimal("100.0"),
    high=Decimal("110.0"),
    low=Decimal("95.0"),
    close=Decimal("105.0"),
    volume=Decimal("1000.0"),
)

_VALID_CONDITION = PreviewCondition(indicator="sma", operator=">", threshold=50.0)


# ── ConditionCompiler negative tests ──────────────────────────────────


class TestConditionCompilerNegative:
    """FD-14.2 — reject invalid inputs at compile time."""

    def test_rejects_non_whitelisted_asset(self) -> None:
        compiler = ConditionCompiler()
        with pytest.raises(ConditionCompileError, match="화이트리스트에 없는 target_asset"):
            compiler.compile(
                strategy_id="s1",
                version="1",
                target_asset="NONEXIST/USDT",
                market="spot",
                exchange="bitget",
                author_agent="agent-1",
                entry_conditions=[_VALID_CONDITION],
                exit_conditions=[],
                stop_loss_conditions=[],
            )

    def test_rejects_empty_entry_conditions(self) -> None:
        compiler = ConditionCompiler()
        with pytest.raises(ConditionCompileError, match="조건이 최소 1개 이상"):
            compiler.compile(
                strategy_id="s1",
                version="1",
                target_asset="BTC/USDT",
                market="spot",
                exchange="bitget",
                author_agent="agent-1",
                entry_conditions=[],
                exit_conditions=[_VALID_CONDITION],
                stop_loss_conditions=[],
            )

    def test_rejects_invalid_combine_mode(self) -> None:
        compiler = ConditionCompiler()
        with pytest.raises(ConditionCompileError, match="지원하지 않는 결합 방식"):
            compiler.compile(
                strategy_id="s1",
                version="1",
                target_asset="BTC/USDT",
                market="spot",
                exchange="bitget",
                author_agent="agent-1",
                entry_conditions=[_VALID_CONDITION],
                exit_conditions=[_VALID_CONDITION],
                stop_loss_conditions=[_VALID_CONDITION],
                entry_combine="XOR",
            )

    def test_rejects_invalid_operator(self) -> None:
        """Pydantic validates operator at PreviewCondition construction time."""
        with pytest.raises(ValidationError, match="literal_error"):
            PreviewCondition(  # pydantic validates at runtime
                indicator="sma",
                operator="contains",
                threshold=100.0,  # noqa: PGH003
            )


# ── CapitalAllocation negative tests ──────────────────────────────────


class TestCapitalAllocationNegative:
    """FD-16.1 — reject invalid allocation requests."""

    @pytest.fixture
    def policy(self) -> StrategyAllocationPolicy:
        return StrategyAllocationPolicy(
            unverified_max_pct=10.0,
            certified_level4_max_pct=25.0,
        )

    def test_rejects_zero_allocated_capital(self, policy: StrategyAllocationPolicy) -> None:
        with pytest.raises(CapitalAllocationError, match="배분 금액은 0보다 커야"):
            validate_capital_allocation(
                allocated_capital=Decimal("0"),
                available_balance=Decimal("1000000"),
                certified_badge=False,
                policy=policy,
            )

    def test_rejects_negative_allocated_capital(self, policy: StrategyAllocationPolicy) -> None:
        with pytest.raises(CapitalAllocationError, match="배분 금액은 0보다 커야"):
            validate_capital_allocation(
                allocated_capital=Decimal("-100"),
                available_balance=Decimal("1000000"),
                certified_badge=False,
                policy=policy,
            )

    def test_rejects_zero_available_balance(self, policy: StrategyAllocationPolicy) -> None:
        with pytest.raises(CapitalAllocationError, match="사용 가능한 잔고가 없습니다"):
            validate_capital_allocation(
                allocated_capital=Decimal("10000"),
                available_balance=Decimal("0"),
                certified_badge=False,
                policy=policy,
            )

    def test_rejects_over_cap_for_unverified(self, policy: StrategyAllocationPolicy) -> None:
        """Unverified strategy: cap is 10% of balance."""
        with pytest.raises(CapitalAllocationError, match="배분 상한 초과"):
            validate_capital_allocation(
                allocated_capital=Decimal("200000"),  # 20% of 1M
                available_balance=Decimal("1000000"),
                certified_badge=False,
                policy=policy,
            )

    def test_rejects_over_cap_for_certified(self, policy: StrategyAllocationPolicy) -> None:
        """Certified strategy: cap is 25% of balance."""
        with pytest.raises(CapitalAllocationError, match="배분 상한 초과"):
            validate_capital_allocation(
                allocated_capital=Decimal("300000"),  # 30% of 1M
                available_balance=Decimal("1000000"),
                certified_badge=True,
                policy=policy,
            )

    def test_allows_within_cap(self, policy: StrategyAllocationPolicy) -> None:
        """Valid allocation within cap should not raise."""
        # 5% of 1M = 50000, within 10% cap for unverified
        validate_capital_allocation(
            allocated_capital=Decimal("50000"),
            available_balance=Decimal("1000000"),
            certified_badge=False,
            policy=policy,
        )

    def test_allocation_cap_pct_returns_correct_value(
        self, policy: StrategyAllocationPolicy
    ) -> None:
        assert allocation_cap_pct(True, policy) == Decimal("25.0")
        assert allocation_cap_pct(False, policy) == Decimal("10.0")


# ── ConditionEvaluation negative tests ────────────────────────────────


class TestConditionEvaluationNegative:
    """Shared condition comparison — reject invalid operators."""

    def test_rejects_invalid_operator(self) -> None:
        with pytest.raises(ValueError, match="지원하지 않는 연산자"):
            compare_value(100.0, "contains", 50.0, None)

    def test_crosses_above_requires_prev_value(self) -> None:
        """crosses_above with prev_value=None should return False (not raise)."""
        assert compare_value(100.0, "crosses_above", 50.0, None) is False

    def test_crosses_below_requires_prev_value(self) -> None:
        """crosses_below with prev_value=None should return False (not raise)."""
        assert compare_value(100.0, "crosses_below", 50.0, None) is False


# ── PreviewCalculator failure-injection tests ─────────────────────────


class TestPreviewCalculatorFailureInjection:
    """FD-14.4 — inject dependency failures."""

    def test_handles_indicator_service_exception(self) -> None:
        """Inject an exception from IndicatorService.calculate — should propagate."""
        calculator = PreviewCalculator()
        conditions = [_VALID_CONDITION]

        with patch.object(
            IndicatorService,
            "calculate",
            side_effect=RuntimeError("indicator backend down"),
        ):
            with pytest.raises(RuntimeError, match="indicator backend down"):
                calculator.preview([_SAMPLE_CANDLE], conditions)

    def test_returns_empty_for_empty_candles(self) -> None:
        """Empty candle list should return empty signals."""
        calculator = PreviewCalculator()
        # Use an empty condition list so no indicator lookup is needed
        result = calculator.preview([], [])
        assert result.signal_indices == []
        assert result.signal_times == []

    def test_returns_empty_for_empty_conditions(self) -> None:
        """Empty condition list should return empty signals."""
        calculator = PreviewCalculator()
        result = calculator.preview([_SAMPLE_CANDLE], [])
        assert result.signal_indices == []
        assert result.signal_times == []


# ── Invariant I-03: no float in monetary amounts ──────────────────────


class TestInvariantFloatMoney:
    """I-03 — monetary amounts must use Decimal, not float."""

    def test_allocation_cap_returns_decimal(self) -> None:
        policy = StrategyAllocationPolicy(
            unverified_max_pct=10.0,
            certified_level4_max_pct=25.0,
        )
        result = allocation_cap_pct(True, policy)
        assert isinstance(result, Decimal)

    def test_validate_uses_decimal_not_float(self) -> None:
        policy = StrategyAllocationPolicy(
            unverified_max_pct=10.0,
            certified_level4_max_pct=25.0,
        )
        # Passing Decimal for both amounts — should work
        validate_capital_allocation(
            allocated_capital=Decimal("100000"),
            available_balance=Decimal("1000000"),
            certified_badge=False,
            policy=policy,
        )
