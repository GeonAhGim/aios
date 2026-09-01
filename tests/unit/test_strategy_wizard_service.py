"""FD-14.2 단위테스트 — 목표기반 전략 생성 마법사, 순수 로직."""

import pytest

from src.services.condition_compiler import ConditionCompiler
from src.services.strategy_wizard_service import (
    GOALS,
    RISK_TOLERANCES,
    StrategyWizardService,
    WizardError,
)


@pytest.fixture
def service():
    return StrategyWizardService()


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("risk_tolerance", RISK_TOLERANCES)
def test_generate_produces_non_empty_condition_groups(service, goal, risk_tolerance):
    result = service.generate(goal, risk_tolerance)

    assert result.entry_conditions
    assert result.exit_conditions
    assert result.stop_loss_conditions
    assert result.explanation


@pytest.mark.parametrize("goal", GOALS)
@pytest.mark.parametrize("risk_tolerance", RISK_TOLERANCES)
def test_generated_conditions_compile_successfully(service, goal, risk_tolerance):
    """마법사 결과물이 실제 실행 엔진(ConditionCompiler)을 통과하는지
    — 조건 스키마를 그대로 재사용한다는 설계가 실제로 맞물리는지 검증."""
    result = service.generate(goal, risk_tolerance)

    compiled = ConditionCompiler().compile(
        strategy_id="wizard-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="wizard-test",
        entry_conditions=result.entry_conditions,
        exit_conditions=result.exit_conditions,
        stop_loss_conditions=result.stop_loss_conditions,
        entry_combine=result.entry_combine,
        exit_combine=result.exit_combine,
        stop_loss_combine=result.stop_loss_combine,
    )

    assert compiled.strategy_id == "wizard-test"


def test_different_risk_tolerances_produce_different_thresholds(service):
    low = service.generate("STEADY_GROWTH", "LOW")
    high = service.generate("STEADY_GROWTH", "HIGH")

    assert low.entry_conditions[0].threshold != high.entry_conditions[0].threshold


def test_rejects_unknown_goal(service):
    with pytest.raises(WizardError):
        service.generate("UNKNOWN_GOAL", "LOW")


def test_rejects_unknown_risk_tolerance(service):
    with pytest.raises(WizardError):
        service.generate("STEADY_GROWTH", "UNKNOWN_RISK")
