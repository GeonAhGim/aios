"""risk_metrics.py DEEPEN — negative/실패주입/성능단언 보강.

task-8406 (원 리프 task-6704 "고아 산출물 회수 5828 qa-2") — 이 패키지의
`__init__.py`는 b1c7d869 이래 항상 빈 파일이었다(회수 대상 files=[]에
이것만 지정됨). 나머지 domain 함수(rules/identity/twr/mwr)는 각자의
`test_*.py`에 이미 negative>=3 + 실패주입 + 성능단언 D2 패턴이 갖춰져
있지만(예: test_twr.py `test_twr_propagates_cashflow_sign_dependency_failure`),
`risk_metrics.py`(변동성/MDD/Sharpe/Calmar)만 boundary-returns-None 케이스만
있고 실패주입·성능단언이 비어 있었다 — 여기서 그 간극을 메운다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest

from src.foundation.performance.domain import risk_metrics
from tests.conftest import PerfBudget

# ---------------------------------------------------------------------------
# Negative test 1: annualized_vol은 음수 periods_per_year를 조용히 틀린
# 숫자(허수/부호반전)로 넘기지 않고 fail-closed로 거부한다.
# ---------------------------------------------------------------------------


def test_annualized_vol_rejects_negative_periods_per_year():
    returns = [Decimal("0.01"), Decimal("0.02"), Decimal("-0.01")]
    with pytest.raises(InvalidOperation):
        risk_metrics.annualized_vol(returns, periods_per_year=-1)


# ---------------------------------------------------------------------------
# Negative test 2: sharpe()는 annualized_vol()에 위임한 연율화 계산의
# 동일한 fail-closed 거부를 그대로 전파해야 한다 — 삼켜서 "데이터 부족"
# 신호인 None으로 둔갑시키면 설정 오류를 데이터 결측으로 오인하게 된다.
# ---------------------------------------------------------------------------


def test_sharpe_propagates_negative_periods_per_year_rejection():
    returns = [Decimal("0.01"), Decimal("0.02"), Decimal("-0.01")]
    with pytest.raises(InvalidOperation):
        risk_metrics.sharpe(returns, rf=Decimal("0"), periods_per_year=-1)


# ---------------------------------------------------------------------------
# Negative test 3: calmar()는 아직 계산 불가한(PENDING) annualized_return을
# 0으로 대체하지 않는다 — identity.py의 "never assume zero" 원칙과 동일.
# 0으로 대체했다면 calmar=0("보상 없음")이라는 명백히 틀린 값을 냈을 것.
# ---------------------------------------------------------------------------


def test_calmar_treats_missing_annualized_return_as_pending_not_zero():
    assert risk_metrics.calmar(None, Decimal("0.1")) is None


# ---------------------------------------------------------------------------
# 실패주입: annualized_vol 의존성이 예외를 던지면 sharpe()가 삼켜서 None으로
# 바꾸지 않고 그대로 전파해야 한다.
# ---------------------------------------------------------------------------


def test_sharpe_does_not_swallow_annualized_vol_dependency_failure(monkeypatch):
    failure = RuntimeError("injected annualized_vol failure")

    def _broken_annualized_vol(returns: list[Decimal], *, periods_per_year: int) -> Decimal:
        raise failure

    monkeypatch.setattr(risk_metrics, "annualized_vol", _broken_annualized_vol)

    with pytest.raises(RuntimeError, match="injected annualized_vol failure") as excinfo:
        risk_metrics.sharpe(
            [Decimal("0.01"), Decimal("0.02")], rf=Decimal("0"), periods_per_year=252
        )
    assert excinfo.value is failure


# ---------------------------------------------------------------------------
# 성능단언: 5천 포인트 규모(§ADR-2026-09-09-C Decision 1 예산표에 순수 산식
# 전용 항목이 없어, twr.py/mwr.py DEEPEN과 같은 근거로 가장 가까운 유사
# 항목의 예산 200ms를 차용) 시계열에서 annualized_vol + max_drawdown 합산
# 호출이 예산 안에 있어야 한다.
# ---------------------------------------------------------------------------

_5K_RETURNS = [Decimal(1000 + (i % 11) - 5) / Decimal(1000) - Decimal(1) for i in range(5000)]
_5K_VALUES = [Decimal(10_000) + Decimal((i % 97) - 48) for i in range(5000)]


def _compute_5k_risk_metrics() -> None:
    risk_metrics.annualized_vol(_5K_RETURNS, periods_per_year=252)
    risk_metrics.max_drawdown(_5K_VALUES)


def test_risk_metrics_5k_boundary_meets_latency_budget(perf_budget: PerfBudget) -> None:
    perf_budget.assert_within(_compute_5k_risk_metrics, budget_ms=200.0, label="risk_metrics(5k)")


def test_risk_metrics_latency_budget_actually_fails_when_regressed(monkeypatch):
    """게이트 적색 재현 — max_drawdown에 인위 지연을 주입하면 위 성능단언이
    tautology가 아니라 실제로 AssertionError를 내는지 확인한다(twr.py
    `test_twr_latency_budget_actually_fails_when_calculation_regresses`와
    같은 패턴). `PerfBudget`은 `time.process_time()`(CPU 시간)을 기준으로
    삼아 `time.sleep()`(GIL을 놓고 대기, CPU 시간을 소비하지 않음)으로는
    재현되지 않는다 — 대신 busy-loop로 실제 CPU를 태운다."""
    original_max_drawdown = risk_metrics.max_drawdown

    def _slow_max_drawdown(values: list[Decimal]) -> Decimal | None:
        total = 0
        for i in range(20_000_000):
            total += i
        return original_max_drawdown(values)

    monkeypatch.setattr(risk_metrics, "max_drawdown", _slow_max_drawdown)

    with pytest.raises(AssertionError):
        PerfBudget().assert_within(
            _compute_5k_risk_metrics, budget_ms=200.0, n=1, warmup=0, label="risk_metrics(5k)"
        )
