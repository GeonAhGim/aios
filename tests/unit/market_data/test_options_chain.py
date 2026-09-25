"""DC-26 evidence: negative, failure-injection, performance, and red-gate checks."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.routers.options_chain import router
from src.api.schemas.options_chain import OptionContractView
from src.foundation.market_data.options.greek_calculator import (
    GreekCalculationUnavailableError,
    GreekCalculator,
    GreekIndicatorRequest,
)


def _contract(**overrides: object) -> OptionContractView:
    values: dict[str, object] = {
        "instrument_id": uuid4(),
        "underlying_id": uuid4(),
        "symbol": "OPT-C",
        "expiry": datetime(2027, 1, 15, tzinfo=timezone.utc),
        "strike": Decimal("100"),
        "option_right": "CALL",
        "contract_multiplier": Decimal("100"),
        "observed_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return OptionContractView.model_validate(values)


def test_chain_rejects_naive_expiry() -> None:
    with pytest.raises(ValidationError):
        _contract(expiry=datetime(2027, 1, 15))


def test_chain_rejects_non_positive_strike() -> None:
    with pytest.raises(ValidationError):
        _contract(strike=Decimal("0"))


def test_chain_rejects_unknown_option_right() -> None:
    with pytest.raises(ValidationError):
        _contract(option_right="STRADDLE")


def test_greek_failure_injection_is_fail_closed() -> None:
    with pytest.raises(GreekCalculationUnavailableError):
        asyncio.run(GreekCalculator().calculate(GreekIndicatorRequest(uuid4(), uuid4())))


@pytest.mark.perf
def test_chain_contract_validation_is_under_budget() -> None:
    start = time.perf_counter()
    for _ in range(500):
        _contract()
    assert time.perf_counter() - start < 0.3


def test_red_gate_reproduction_route_is_registered() -> None:
    # Regression guard for the ghost-path gate: endpoint must remain mounted
    # at the spec path, not only exist as an unregistered router function.
    paths = {route.path for route in router.routes}
    assert "/v1/foundation/market-data/options/chain" in paths
