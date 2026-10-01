"""DC-26 evidence: module-level exports, negative, and failure-injection checks."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.foundation.market_data import (
    GreekCalculationUnavailableError,
    GreekCalculator,
    GreekIndicatorRequest,
    __all__,
)


def test_calculator_without_delegate_raises() -> None:
    """Negative: GreekCalculator without IND delegate must fail closed."""
    calc = GreekCalculator()
    request = GreekIndicatorRequest(uuid4(), uuid4())
    with pytest.raises(GreekCalculationUnavailableError):
        asyncio.run(calc.calculate(request))


def test_greek_calculation_error_is_runtime_error() -> None:
    """Negative: GreekCalculationUnavailableError must be a RuntimeError subclass."""
    assert issubclass(GreekCalculationUnavailableError, RuntimeError)


def test_greek_indicator_request_default_name() -> None:
    """Negative: GreekIndicatorRequest defaults indicator_name to 'option_greeks'."""
    request = GreekIndicatorRequest(uuid4(), uuid4())
    assert request.indicator_name == "option_greeks"


def test_delegate_exception_propagates() -> None:
    """Failure injection: delegate raises RuntimeError — must propagate."""

    class BrokenDelegate:
        async def request_greeks(self, request: GreekIndicatorRequest) -> str:
            raise RuntimeError("IND service unavailable")

    calc = GreekCalculator(delegate=BrokenDelegate())
    request = GreekIndicatorRequest(uuid4(), uuid4())
    with pytest.raises(RuntimeError, match="IND service unavailable"):
        asyncio.run(calc.calculate(request))


def test_all_exports_are_accessible() -> None:
    """Invariant: all __all__ members must be importable from the package."""
    expected = {
        "GreekCalculationUnavailableError",
        "GreekCalculator",
        "GreekIndicatorRequest",
    }
    assert set(__all__) == expected
