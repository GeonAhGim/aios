"""DC-26 evidence: negative, failure-injection, performance, and red-gate checks."""

from __future__ import annotations

import asyncio
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
def test_chain_contract_validation_is_under_budget(perf_budget) -> None:
    """task-11810 — raw perf_counter → perf_budget.assert_within(batch=500, budget_ms=500).

    batch=500: process_time() Windows tick ≈15.6 ms이므로 1회 측정만 하면 0/15.6만 나온다.
    500회를 묶어 batch 평균을 내면 tick 오차가 15.6/500 ≈ 0.03 ms로 떨어진다.
    단위: budget_ms=500 (CI 병렬 실행 CPU 경합으로 300ms가 불안정, task-15360).

    검출력: budget을 10배로 늘린 5000 ms 주입 지연 테스트는 적색(5.0초 이상 걸림).
    """
    perf_budget.assert_within(
        lambda: _contract(),
        budget_ms=500,
        batch=500,
    )


def test_red_gate_reproduction_route_is_registered() -> None:
    # Regression guard for the ghost-path gate: endpoint must remain mounted
    # at the spec path, not only exist as an unregistered router function.
    paths = {route.path for route in router.routes}
    assert "/v1/foundation/market-data/options/chain" in paths
