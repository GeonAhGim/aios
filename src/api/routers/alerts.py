"""FD-14(신설) — 가격/지표 알림 API 라우터.

PLT-20 — raw HTTPException 제거. AlertError/AlertNotFoundError는 이제
전역 핸들러(src/api/contracts/handlers.py)가 EXCEPTION_MAP을 통해
상태코드·error_code·trace_id를 채운다(§9 PLT-17~21).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from src.api.deps import get_current_user
from src.api.schemas.alerts import AlertCreateRequest
from src.api.service_deps import get_alert_service
from src.services.alert_service import AlertService, PriceAlert
from src.services.auth_service import User

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_alert(
    body: AlertCreateRequest,
    user: User = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> PriceAlert:
    return await service.create_alert(
        user.user_id,
        exchange=body.exchange,
        symbol=body.symbol,
        timeframe=body.timeframe,
        indicator=body.indicator,
        params=body.params,
        operator=body.operator,
        threshold=body.threshold,
    )


@router.get("")
async def list_my_alerts(
    user: User = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> list[PriceAlert]:
    return await service.list_my_alerts(user.user_id)


@router.post("/{alert_id}/cancel")
async def cancel_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> PriceAlert:
    return await service.cancel_alert(user.user_id, alert_id)
