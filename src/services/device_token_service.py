"""21.1 — Device push token registration (DeviceTokenService).

Spec: 기능설계문서_v1.20.md#FD-21.1, FD-17.1

FD-17.1 Gateway PUSH channel dispatch function (SendChannelFn; actual
APNs/FCM calls are out of this session scope — still externally injected
via DI). list_active_tokens() is called later to determine which devices
to target.

Only active tokens are unique (partial unique index) — re-registration
after deactivation is allowed (not blocked). On send failure (token
expired, etc.) deactivate() marks the token inactive — this is separate
from the FD-17.1 CRITICAL retry policy (retrying a token-level issue
has no effect).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import BaseModel

VALID_PLATFORMS = ("iOS", "Android")


class DeviceTokenError(Exception):
    """FD-21.1 Registration failure (unknown platform) — maps to VALIDATION_INVALID_FIELD(400)."""


class DeviceTokenNotFoundError(DeviceTokenError):
    """No device to revoke or already inactive — kept as a separate subclass so
    exception_mapping.py EXCEPTION_MAP can distinguish it with RESOURCE_NOT_FOUND(404)
    (EXCEPTION_MAP is type-based; a single class can only map to one status code — same
    convention as PLT-17's ExchangeCredentialNotFoundError)."""


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
        """Discovered on first HTTP exposure in this session — without user_id
        ownership check, any attacker could guess a device_id belonging to another
        user and revoke it (IDOR)."""
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
