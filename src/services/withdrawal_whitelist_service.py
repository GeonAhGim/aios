"""11.7 — Emergency withdrawal destination whitelist management.

Spec: 기능설계문서_v1.20.md#FD-11.5, 정책문서 7.10-A/20.1-B

Pre-registers destinations that FD-10.3 (panic prompt) will reference
during a crisis — to genuinely enforce "registration itself becomes
impossible after a crisis hits", normal-state and crisis-state must be
distinguishable.

Interpretation (FD-9 integration): FD-9 has no separate persistent flag
pointing to FD-11.5's "FD-9-detected counterparty risk severity signal
is active" — reuse Circuit Breaker (FD-9.4) RESTRICTED-or-above levels
as "crisis state" (same principle as 9.6 Reconciliation reusing the
same infrastructure — do not invent a new state).

destination_address is stored encrypted with AES-256-GCM
(src/core/security/encryption.py, reusing 07 §7.3 CREDENTIAL_ENCRYPTION_KEY).
The delete (revoke) feature is intentionally not provided, exactly as
specified in 04.

FD-17.1 event publishing — on successful registration, publishes
"security.withdrawal_whitelist.added" (4.9 mandatory rule — this
channel cannot be disabled by the user, channel_policy.py).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.approval.panic_prompt import WhitelistEntry
from src.core.logging.audit_log import record_audit_log
from src.core.safety.circuit_breaker import CircuitBreakerLevel, CircuitBreakerService
from src.core.security.encryption import legacy_decrypt, legacy_encrypt

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_CRISIS_LEVELS = {
    CircuitBreakerLevel.RESTRICTED,
    CircuitBreakerLevel.HALTED,
    CircuitBreakerLevel.EMERGENCY,
}


class WithdrawalWhitelistError(Exception):
    """FD-11.5 registration rejected — router converts to 409."""


class WithdrawalWhitelistEntry(BaseModel):
    id: int
    exchange: str
    destination_address: str
    label: str | None


class WithdrawalWhitelistService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        circuit_breaker: CircuitBreakerService,
        *,
        encryption_key: str,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._circuit_breaker = circuit_breaker
        self._encryption_key = encryption_key
        self._publish = publish

    async def register(
        self,
        user_id: UUID,
        *,
        exchange: str,
        destination_address: str,
        label: str | None = None,
    ) -> WithdrawalWhitelistEntry:
        state = await self._circuit_breaker.get_state()
        if state.level in _CRISIS_LEVELS:
            raise WithdrawalWhitelistError(
                "위기 상황 중에는 새 목적지를 등록할 수 없습니다. 평상시에 미리 등록해주세요."
            )

        encrypted_address = legacy_encrypt(destination_address, self._encryption_key)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO withdrawal_whitelist (user_id, exchange, destination_address, label) "
                "VALUES ($1, $2, $3, $4) RETURNING id, exchange, label",
                user_id,
                exchange,
                encrypted_address,
                label,
            )
            # Never leave destination_address in plaintext — it is the actual
            # withdrawal destination and must not appear even in audit_log
            # (record only which exchange, label, and result).
            await record_audit_log(
                conn, actor_agent=str(user_id), action_type="withdrawal_whitelist.registered",
                user_id=user_id,
                decision_data={"exchange": exchange, "label": label, "entry_id": row["id"]},
            )
        if self._publish is not None:
            await self._publish(
                "security.withdrawal_whitelist.added",
                {
                    "event_type": "security.withdrawal_whitelist.added",
                    "user_id": str(user_id),
                    "exchange": exchange,
                },
            )

        return WithdrawalWhitelistEntry(
            id=row["id"],
            exchange=row["exchange"],
            destination_address=destination_address,
            label=row["label"],
        )

    async def list_for_user(self, user_id: UUID) -> list[WithdrawalWhitelistEntry]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, exchange, destination_address, label FROM withdrawal_whitelist "
                "WHERE user_id = $1 ORDER BY created_at",
                user_id,
            )
        return [
            WithdrawalWhitelistEntry(
                id=row["id"],
                exchange=row["exchange"],
                destination_address=legacy_decrypt(
                    row["destination_address"], self._encryption_key
                ),
                label=row["label"],
            )
            for row in rows
        ]

    async def fetch_for_panic_prompt(self, user_id: UUID, exchange: str) -> list[WhitelistEntry]:
        """Convert to FD-10.3 PanicPromptGenerator fetch_whitelist DI callback
        signature (user_id, exchange) -> list[WhitelistEntry] — wiring
        happens at app assembly stage (16)."""
        entries = await self.list_for_user(user_id)
        return [
            WhitelistEntry(
                id=entry.id,
                exchange=entry.exchange,
                destination_address=entry.destination_address,
                label=entry.label,
            )
            for entry in entries
            if entry.exchange == exchange
        ]
