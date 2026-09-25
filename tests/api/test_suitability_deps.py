"""Tests for src/api/suitability_deps.py dependency injection.

Covers:
- Happy-path: get_suitability_questionnaire returns instance, get_risk_profile_service
  creates RiskProfileService with the pool
- Negative: pool is None (stored silently, used later), pool invalid type
- Failure injection: constructor raises
- Boundary: multiple calls create independent instances
"""

from unittest.mock import MagicMock, patch

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


# ── Failure injection tests ───────────────────────────────────────────────────


def test_get_risk_profile_service_constructor_raises():
    """RiskProfileService.__init__ raises → exception propagates to caller."""
    with patch(
        "src.api.suitability_deps.RiskProfileService",
        side_effect=RuntimeError("DB connection lost"),
    ) as mock_cls:
        mock_pool = MagicMock()
        with pytest.raises(RuntimeError, match="DB connection lost"):
            get_risk_profile_service(pool=mock_pool)
        # Source passes pool as positional arg: RiskProfileService(pool)
        mock_cls.assert_called_once_with(mock_pool)


def test_get_suitability_questionnaire_constructor_raises():
    """SuitabilityQuestionnaire constructor raises → exception propagates."""
    with patch(
        "src.api.suitability_deps.SuitabilityQuestionnaire",
        side_effect=RuntimeError("questionnaire init failed"),
    ) as mock_cls:
        with pytest.raises(RuntimeError, match="questionnaire init failed"):
            get_suitability_questionnaire()
        mock_cls.assert_called_once()


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
