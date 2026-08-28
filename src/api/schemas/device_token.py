"""21번 — 디바이스 토큰 API 요청 스키마."""
from __future__ import annotations

from pydantic import BaseModel


class DeviceTokenRegisterRequest(BaseModel):
    device_token: str
    platform: str
