"""15.2 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.risk_profile_service import RiskProfileError, RiskProfileService
from src.services.suitability_questionnaire import (
    InvestmentGoal,
    LiquidityNeed,
    SuitabilityAnswers,
    SuitabilityQuestionnaire,
)
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return RiskProfileService(pool)


def _answers(**overrides):
    defaults = {
        "years_of_experience": 0,
        "investable_ratio_pct": 5,
        "loss_tolerance_pct": 5,
        "investment_goal": InvestmentGoal.LONG_TERM_GROWTH,
        "liquidity_need": LiquidityNeed.WITHIN_1_YEAR,
    }
    defaults.update(overrides)
    return SuitabilityAnswers(**defaults)


def _aggressive_answers():
    return _answers(
        years_of_experience=15,
        investable_ratio_pct=80,
        loss_tolerance_pct=50,
        investment_goal=InvestmentGoal.SHORT_TERM_PROFIT,
        liquidity_need=LiquidityNeed.OVER_THREE_YEARS,
    )


async def test_save_assessment_persists_to_users_table(service, pool):
    user_id = await create_test_user(pool)
    result = SuitabilityQuestionnaire().evaluate(_answers())

    record = await service.save_assessment(user_id, result)

    assert record.risk_profile == result.risk_profile
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT risk_profile, risk_profile_assessed_at FROM users WHERE user_id = $1",
            user_id,
        )
    assert row["risk_profile"] == result.risk_profile
    assert row["risk_profile_assessed_at"] is not None


async def test_save_assessment_appends_history_without_overwriting(service, pool):
    user_id = await create_test_user(pool)
    first = SuitabilityQuestionnaire().evaluate(_answers())
    await service.save_assessment(user_id, first)

    second = SuitabilityQuestionnaire().evaluate(_aggressive_answers())
    await service.save_assessment(user_id, second)

    history = await service.get_history(user_id)
    assert len(history) == 2
    assert history[0]["risk_profile"] == first.risk_profile
    assert history[1]["risk_profile"] == second.risk_profile


async def test_reassessment_to_higher_risk_flagged(service, pool):
    user_id = await create_test_user(pool)
    stable = SuitabilityQuestionnaire().evaluate(_answers())
    await service.save_assessment(user_id, stable)

    aggressive = SuitabilityQuestionnaire().evaluate(_aggressive_answers())
    record = await service.save_assessment(user_id, aggressive)

    assert record.is_higher_risk_than_previous is True


async def test_reassessment_to_same_or_lower_risk_not_flagged(service, pool):
    user_id = await create_test_user(pool)
    aggressive = SuitabilityQuestionnaire().evaluate(_aggressive_answers())
    await service.save_assessment(user_id, aggressive)

    stable = SuitabilityQuestionnaire().evaluate(_answers())
    record = await service.save_assessment(user_id, stable)

    assert record.is_higher_risk_than_previous is False


async def test_first_assessment_never_flagged_as_higher_risk(service, pool):
    user_id = await create_test_user(pool)
    result = SuitabilityQuestionnaire().evaluate(_aggressive_answers())

    record = await service.save_assessment(user_id, result)

    assert record.is_higher_risk_than_previous is False


async def test_get_current_returns_none_before_assessment(service, pool):
    user_id = await create_test_user(pool)

    assert await service.get_current(user_id) is None


async def test_save_assessment_rejects_nonexistent_user(service):
    import uuid

    result = SuitabilityQuestionnaire().evaluate(_answers())
    with pytest.raises(RiskProfileError):
        await service.save_assessment(uuid.uuid4(), result)
