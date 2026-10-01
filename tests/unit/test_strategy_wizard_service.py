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


# ── negative tests (empty / type / case mismatch) ──────────────────


def test_rejects_empty_goal(service):
    """빈 문자열 goal 전달 시 WizardError 발생 — I-07 hard-fail 검증."""
    with pytest.raises(WizardError):
        service.generate("", "LOW")


def test_rejects_case_mismatch_goal(service):
    """소문자 goal("hedge") 전달 시 WizardError 발생 — GOALS는 대문자만 허용."""
    with pytest.raises(WizardError):
        service.generate("hedge", "LOW")


def test_rejects_int_goal(service):
    """int 타입 goal 전달 시 TypeError 발생 — 명시적 타입 검증."""
    with pytest.raises((TypeError, WizardError)):
        service.generate(123, "LOW")


def test_rejects_extreme_risk_tolerance(service):
    """범위 밖 risk_tolerance("EXTREME") 전달 시 WizardError 발생 — I-07."""
    with pytest.raises(WizardError):
        service.generate("STEADY_GROWTH", "EXTREME")


# ── failure-injection test ─────────────────────────────────────────


def test_handles_preview_condition_creation_failure(service):
    """_rsi 호출이 예외를 던지도록 monkeypatch — 서비스가 예외를
    삼키지 않고 전파함을 확인 (I-07 fail-closed)."""
    import src.services.strategy_wizard_service as mod

    original_rsi = mod._rsi
    call_log: list[int] = []

    def failing_rsi(threshold, operator):
        call_log.append(1)
        raise ValueError("indicator service unavailable")

    mod._rsi = failing_rsi
    try:
        with pytest.raises(ValueError, match="indicator service unavailable"):
            service.generate("STEADY_GROWTH", "LOW")
        assert call_log, "_rsi가 호출되었으나 예외가 던져지지 않음"
    finally:
        mod._rsi = original_rsi
