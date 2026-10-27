"""Tests for src/api/suitability_deps.py dependency injection.

Covers:
- Happy-path: get_suitability_questionnaire returns instance, get_risk_profile_service
  creates RiskProfileService with the pool
- Negative: pool is None (stored silently, used later), pool invalid type, zero/negative
  answer values, extreme ratios
- Failure injection: real objects with dependency failures (pool cursor raises,
  questionnaire evaluate with extreme answers)
- Boundary: multiple calls create independent instances
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.suitability_deps import (
    get_risk_profile_service,
    get_suitability_questionnaire,
)

# ── Happy-path tests ─────────────────────────────────────────────────────────


def test_get_suitability_questionnaire_returns_instance():
    """get_suitability_questionnaire returns a SuitabilityQuestionnaire instance."""
    result = get_suitability_questionnaire()
    from src.services.suitability_questionnaire import SuitabilityQuestionnaire

    assert isinstance(result, SuitabilityQuestionnaire)


def test_get_risk_profile_service_with_pool():
    """get_risk_profile_service creates RiskProfileService with the pool."""
    mock_pool = MagicMock()
    result = get_risk_profile_service(pool=mock_pool)
    from src.services.risk_profile_service import RiskProfileService

    assert isinstance(result, RiskProfileService)
    # Verify the pool was stored (attribute is _pool)
    assert result._pool is mock_pool


# ── Negative tests ────────────────────────────────────────────────────────────


def test_get_risk_profile_service_pool_none():
    """Pool is None → RiskProfileService stores None (validated later at use time)."""
    # RiskProfileService.__init__ does NOT validate — it stores _pool silently.
    # The None pool will only cause errors when methods like save_assessment are called.
    result = get_risk_profile_service(pool=None)
    assert result._pool is None


def test_get_risk_profile_service_pool_invalid_type():
    """Pool is not an asyncpg pool (e.g. a plain dict) → RiskProfileService accepts it."""
    bad_pool = {"not": "a pool"}
    result = get_risk_profile_service(pool=bad_pool)
    assert result._pool is bad_pool


# ── Failure injection tests (real objects, dependency-level injection) ────────


def test_risk_profile_service_save_assessment_pool_cursor_raises():
    """Pool.acquire() raises during save_assessment → exception propagates unchanged."""
    import asyncio
    from uuid import uuid4

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock()
    # Simulate pool.acquire() context manager raising on enter
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(
        side_effect=ConnectionError("pool connection dropped")
    )
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    svc = get_risk_profile_service(pool=mock_pool)
    user_id = uuid4()

    with pytest.raises(ConnectionError, match="pool connection dropped"):
        asyncio.run(svc.save_assessment(user_id, MagicMock()))


def test_questionnaire_evaluate_zero_experience_returns_stable():
    """Zero/negative experience + minimal inputs → score 3 (goal only) → stable (edge-case)."""
    from src.services.suitability_questionnaire import (
        InvestmentGoal,
        LiquidityNeed,
        SuitabilityAnswers,
    )

    q = get_suitability_questionnaire()
    answers = SuitabilityAnswers(
        years_of_experience=0,
        investable_ratio_pct=0,
        loss_tolerance_pct=0,
        investment_goal=InvestmentGoal.SHORT_TERM_PROFIT,
        liquidity_need=LiquidityNeed.WITHIN_1_YEAR,
    )
    result = q.evaluate(answers)
    # years=0→0, ratio=0→0, loss=0→0, goal=SHORT→3, liq=WITHIN→0 = 3 → stable
    assert result.score == 3
    assert result.risk_profile == "안정형"


def test_questionnaire_evaluate_max_values_returns_aggressive():
    """Max experience + max ratios + short-term → aggressive (upper-bound injection)."""
    from src.services.suitability_questionnaire import (
        InvestmentGoal,
        LiquidityNeed,
        SuitabilityAnswers,
    )

    q = get_suitability_questionnaire()
    answers = SuitabilityAnswers(
        years_of_experience=100,
        investable_ratio_pct=100,
        loss_tolerance_pct=100,
        investment_goal=InvestmentGoal.SHORT_TERM_PROFIT,
        liquidity_need=LiquidityNeed.OVER_THREE_YEARS,
    )
    result = q.evaluate(answers)
    # years>10→3, ratio>60→3, loss>30→3, goal=SHORT→3, liq=OVER→3 = 15 → aggressive
    assert result.score == 15
    assert result.risk_profile == "공격형"


# ── Boundary tests ────────────────────────────────────────────────────────────


def test_multiple_questionnaire_calls_create_independent_instances():
    """Each call to get_suitability_questionnaire creates a new instance."""
    a = get_suitability_questionnaire()
    b = get_suitability_questionnaire()
    assert a is not b


def test_multiple_pool_calls_create_independent_instances():
    """Each call to get_risk_profile_service creates a new instance."""
    mock_pool = MagicMock()
    a = get_risk_profile_service(pool=mock_pool)
    b = get_risk_profile_service(pool=mock_pool)
    assert a is not b
    # Both should reference the same pool
    assert a._pool is b._pool is mock_pool


def test_suitability_questionnaire_evaluate_works():
    """The returned questionnaire can actually evaluate answers."""
    from src.services.suitability_questionnaire import (
        InvestmentGoal,
        LiquidityNeed,
        SuitabilityAnswers,
    )

    q = get_suitability_questionnaire()
    answers = SuitabilityAnswers(
        years_of_experience=5,
        investable_ratio_pct=50,
        loss_tolerance_pct=20,
        investment_goal=InvestmentGoal.LONG_TERM_GROWTH,
        liquidity_need=LiquidityNeed.ONE_TO_THREE_YEARS,
    )
    result = q.evaluate(answers)
    assert result.score >= 0
    assert result.risk_profile in ("안정형", "중립형", "공격형")
    assert result.answers is answers


# ── Additional negative tests (wiring) ────────────────────────────────────────


def test_get_risk_profile_service_pool_default_is_not_a_pool():
    """Unwired call (no pool arg) → pool default is the Depends marker, not a real pool."""
    import inspect

    from fastapi import params

    from src.api.deps import get_pool

    default = inspect.signature(get_risk_profile_service).parameters["pool"].default
    assert isinstance(default, params.Depends)
    assert default.dependency is get_pool


# ── Performance assertion (D2, via perf_budget fixture) ───────────────────────


def test_dependency_factories_within_latency_budget(perf_budget):
    """Both factories are pure object construction — p-budget check via shared perf helper."""
    mock_pool = MagicMock()
    perf_budget.assert_within(get_suitability_questionnaire, budget_ms=20.0)
    perf_budget.assert_within(lambda: get_risk_profile_service(pool=mock_pool), budget_ms=20.0)
