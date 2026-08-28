"""21번대 — 디바이스 토큰 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.services.device_token_service import DeviceTokenService

from .deps import get_pool


def get_device_token_service(pool: asyncpg.Pool = Depends(get_pool)) -> DeviceTokenService:
    return DeviceTokenService(pool)
