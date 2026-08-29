"""15번 — 투자자 적합성평가 API 라우터 (FD-15.1/FD-15.2).

Spec: 기능설계문서_v1.20.md#FD-15.1/FD-15.2, 16_backend_signatures.md §16.5

FD-15.3(위험등급-전략 매칭 경고)은 별도 엔드포인트가 없다 — 마켓플레이스
구매(FD-13.3, 이미 배선됨)와 전략 배포 승인(FD-14.3, 자동 파이프라인
전용으로 미노출) 두 지점에서 훅으로만 작동하는 것이 원문 설계다. 단,
재평가로 등급이 나빠진 경우(FD-15.2 예외상황)만은 예외로 이 라우터가
직접 처리한다 — RiskProfileService.save_assessment() 작성 당시엔 FD-16
(strategy_executions)이 없어 대조 자체가 불가능했지만, 이제 있으므로
is_higher_risk_than_previous일 때 RUNNING 실행을 대조해 즉시 경고를
발행한다("다음 화면 진입 시가 아니라 즉시" — FD-15.2 원문).
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

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
from src.services.risk_profile_service import RiskProfileService
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
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "아직 적합성평가를 완료하지 않았습니다."
        )
    return to_risk_profile_response(record)


@router.get("/risk-profile/history")
async def get_risk_profile_history(
    user: User = Depends(get_current_user),
    service: RiskProfileService = Depends(get_risk_profile_service),
) -> list[RiskProfileHistoryEntry]:
    rows = await service.get_history(user.user_id)
    return [to_history_entry(row) for row in rows]
