"""21.1 — 디바이스 푸시 토큰 등록 (DeviceTokenService).

Spec: 기능설계문서_v1.20.md#FD-21.1, FD-17.1

FD-17.1 게이트웨이의 PUSH 채널 발송 함수(SendChannelFn, 실제 APNs/FCM
호출은 이 세션 스콥 밖 — 여전히 DI로 외부 주입)가 나중에 이
list_active_tokens()를 호출해 어느 디바이스로 보낼지 조회하게 된다.

활성 토큰끼리만 유니크(부분 유니크 인덱스) — 해지 후 같은 토큰으로
재등록해도 막히지 않는다. 발송 실패 시(토큰 만료 등) deactivate()로
비활성화한다 — FD-17.1의 CRITICAL 재시도 정책과 별개(토큰 자체 문제는
재시도해도 의미 없음).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import BaseModel

VALID_PLATFORMS = ("iOS", "Android")


class DeviceTokenError(Exception):
    """FD-21.1 등록 실패(알 수 없는 platform) — VALIDATION_INVALID_FIELD(400)."""


class DeviceTokenNotFoundError(DeviceTokenError):
    """해지하려는 디바이스가 없거나 이미 비활성화됨 — RESOURCE_NOT_FOUND(404)로
    구분 매핑되도록 별도 서브클래스를 둔다(exception_mapping.py EXCEPTION_MAP은
    타입 기반이라 같은 클래스면 상태코드를 하나로만 고를 수 있다 — PLT-17의
    ExchangeCredentialNotFoundError와 동일 관행)."""


class DeviceTokenRecord(BaseModel):
    device_id: int
    registered_at: datetime
    is_active: bool


class DeviceTokenService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def register(
        self, user_id: UUID, device_token: str, platform: str
    ) -> DeviceTokenRecord:
        if platform not in VALID_PLATFORMS:
            raise DeviceTokenError(f"알 수 없는 platform입니다: {platform}")

        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, registered_at FROM device_tokens "
                "WHERE user_id = $1 AND device_token = $2 AND is_active",
                user_id,
                device_token,
            )
            if existing is not None:
                return DeviceTokenRecord(
                    device_id=existing["id"],
                    registered_at=existing["registered_at"],
                    is_active=True,
                )

            row = await conn.fetchrow(
                "INSERT INTO device_tokens (user_id, device_token, platform) "
                "VALUES ($1, $2, $3) RETURNING id, registered_at",
                user_id,
                device_token,
                platform,
            )
        return DeviceTokenRecord(
            device_id=row["id"], registered_at=row["registered_at"], is_active=True
        )

    async def deactivate(self, device_id: int, user_id: UUID) -> None:
        """이 세션에서 처음 HTTP로 노출하며 발견 — user_id 소유권 확인 없이는
        다른 사용자의 device_id를 추측해 해지시킬 수 있었다(IDOR)."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE device_tokens SET is_active = false "
                "WHERE id = $1 AND user_id = $2 AND is_active",
                device_id,
                user_id,
            )
        if result == "UPDATE 0":
            raise DeviceTokenNotFoundError("존재하지 않거나 이미 비활성화된 디바이스입니다.")

    async def list_active_tokens(self, user_id: UUID) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT device_token FROM device_tokens WHERE user_id = $1 AND is_active",
                user_id,
            )
        return [row["device_token"] for row in rows]
