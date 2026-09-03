"""21번 — 디바이스 푸시 토큰 API 라우터 (FD-21.1).

Spec: 기능설계문서_v1.20.md#FD-21.1, FD-17.1

list_active_tokens()는 이 엔드포인트에 없다 — FD-17.1 알림 게이트웨이가
발송 시점에 내부적으로 호출하는 조회이지 사용자 대면 API가 아니다.

PLT-20 — raw HTTPException 제거. DeviceTokenError/DeviceTokenNotFoundError는
이제 전역 핸들러(src/api/contracts/handlers.py)가 EXCEPTION_MAP을 통해
상태코드·error_code·trace_id를 채운다(§9 PLT-17~21).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from src.api.deps import get_current_user
from src.api.device_token_deps import get_device_token_service
from src.api.schemas.device_token import DeviceTokenRegisterRequest
from src.services.auth_service import User
from src.services.device_token_service import DeviceTokenRecord, DeviceTokenService

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_device_token(
    body: DeviceTokenRegisterRequest,
    user: User = Depends(get_current_user),
    service: DeviceTokenService = Depends(get_device_token_service),
) -> DeviceTokenRecord:
    return await service.register(user.user_id, body.device_token, body.platform)


@router.delete("/{device_id}")
async def deactivate_device_token(
    device_id: int,
    user: User = Depends(get_current_user),
    service: DeviceTokenService = Depends(get_device_token_service),
) -> dict[str, str]:
    await service.deactivate(device_id, user.user_id)
    return {"device_id": str(device_id), "status": "deactivated"}
