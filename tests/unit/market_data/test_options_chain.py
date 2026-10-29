"""DC-26 evidence: negative, failure-injection, performance, and red-gate checks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
from tests.support.perf_budget import PerfBudget


def _contract(**overrides: object) -> OptionContractView:
    values: dict[str, object] = {
        "instrument_id": uuid4(),
        "underlying_id": uuid4(),
        "symbol": "OPT-C",
        "expiry": datetime(2027, 1, 15, tzinfo=UTC),
        "strike": Decimal("100"),
        "option_right": "CALL",
        "contract_multiplier": Decimal("100"),
        "observed_at": datetime(2026, 9, 21, tzinfo=UTC),
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
    """task-11810 — raw perf_counter → perf_budget.assert_within(batch=500, budget_ms=800).

    batch=500: process_time() Windows tick ≈15.6 ms이므로 1회 측정만 하면 0/15.6만 나온다.
    500회를 묶어 batch 평균을 내면 tick 오차가 15.6/500 ≈ 0.03 ms로 떨어진다.
    단위: budget_ms=800 (CI 병렬 실행 CPU 경합으로 500ms가 불안정, task-15369).

    검출력: budget을 10배로 늘린 8000 ms 주입 지연 테스트는 적색(8.0초 이상 걸림).
    """
    perf_budget.assert_within(
        lambda: _contract(),
        budget_ms=800,
        batch=500,
    )


def test_red_gate_reproduction_route_is_registered() -> None:
    # Regression guard for the ghost-path gate: endpoint must remain mounted
    # at the spec path, not only exist as an unregistered router function.
    paths = {route.path for route in router.routes}
    assert "/v1/foundation/market-data/options/chain" in paths


# --- gate-red reproduction (D2) — 10x delay injection -----------------------


@pytest.mark.perf
def test_chain_contract_validation_perf_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    예산(800ms)을 10배 초과하는 CPU 시간을 가장하면
    `assert_within`가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    budget_ms = 800.0

    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 10초(10x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 10_000.0  # 10 000ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: _contract(),
            budget_ms=budget_ms,
            batch=500,
            label="contract validation delay-injected",
        )
