"""Leaf 15 — Investor Suitability Assessment API Router (FD-15.1/FD-15.2).

Spec: 기능설계문서_v1.20.md#FD-15.1/FD-15.2, 16_backend_signatures.md §16.5

FD-15.3 (risk-grade strategy matching warning) has no dedicated endpoint — the
original design hooks into only two call sites: marketplace purchase (FD-13.3,
already wired) and strategy deployment approval (FD-14.3, hidden behind an
auto-pipeline).  Exception: when a re-assessment downgrades the risk grade
(FD-15.2 edge case), this router handles it directly.  At the time
RiskProfileService.save_assessment() was written, FD-16 (strategy_executions)
did not yet exist, making cross-checking impossible; now that it does, we
compare against RUNNING executions when
is_higher_risk_than_previous is true and issue an immediate warning
("immediately, not on next screen entry" — FD-15.2 original text).

PLT-18 — Migrated raw ``HTTPException`` raises to domain exceptions
(RiskProfileNotFoundError in risk_profile_service.py) (§9 PLT-17~21).
``RiskProfileService.get_current()`` retains its existing contract of returning
``None`` (other tests without callers assume this), and this router raises the
domain exception directly when ``None`` is returned.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, status

from src.api.deps import get_current_user, get_event_bus, get_pool
from src.api.schemas.suitability import (
    RiskProfileHistoryEntry,
    RiskProfileResponse,
    to_history_entry,
    to_risk_profile_response,
)
from src.api.suitability_deps import get_risk_profile_service, get_suitability_questionnaire
from src.core.event_bus.bus import EventBus
from src.services.auth_service import User
from src.services.risk_matching import find_running_execution_mismatches
from src.services.risk_profile_service import RiskProfileNotFoundError, RiskProfileService
from src.services.suitability_questionnaire import SuitabilityAnswers, SuitabilityQuestionnaire

router = APIRouter(prefix="/users/me", tags=["suitability"])


@router.post("/risk-assessment", status_code=status.HTTP_201_CREATED)
async def submit_assessment(
    body: SuitabilityAnswers,
    user: User = Depends(get_current_user),
    questionnaire: SuitabilityQuestionnaire = Depends(get_suitability_questionnaire),
    service: RiskProfileService = Depends(get_risk_profile_service),
    pool: asyncpg.Pool = Depends(get_pool),
    event_bus: EventBus = Depends(get_event_bus),
) -> RiskProfileResponse:
    result = questionnaire.evaluate(body)
    record = await service.save_assessment(user.user_id, result)

    if record.is_higher_risk_than_previous:
        mismatched = await find_running_execution_mismatches(
            pool, user.user_id, record.risk_profile
        )
        for strategy_id in mismatched:
            await event_bus.publish(
                "risk_profile.match.warned",
                {
                    "event_type": "risk_profile.match.warned",
                    "user_id": str(user.user_id),
                    "strategy_id": strategy_id,
                    "reason": "재평가로 위험등급이 상향돼 실행 중인 전략과 불일치합니다.",
                },
            )

    return to_risk_profile_response(record)


@router.get("/risk-profile")
async def get_risk_profile(
    user: User = Depends(get_current_user),
    service: RiskProfileService = Depends(get_risk_profile_service),
) -> RiskProfileResponse:
    record = await service.get_current(user.user_id)
    if record is None:
        raise RiskProfileNotFoundError("아직 적합성평가를 완료하지 않았습니다.")
    return to_risk_profile_response(record)


@router.get("/risk-profile/history")
async def get_risk_profile_history(
    user: User = Depends(get_current_user),
    service: RiskProfileService = Depends(get_risk_profile_service),
) -> list[RiskProfileHistoryEntry]:
    rows = await service.get_history(user.user_id)
    return [to_history_entry(row) for row in rows]
