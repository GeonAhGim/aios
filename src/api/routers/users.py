"""FD-11.1 — 현재 사용자 조회 API 라우터.

Spec: 16_backend_signatures.md
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.schemas.auth import UserResponse, to_user_response
from src.services.auth_service import User

router = APIRouter()


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)) -> UserResponse:
    return to_user_response(user)
