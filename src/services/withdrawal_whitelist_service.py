"""11.7 — 비상 출금 목적지 화이트리스트 관리.

Spec: 기능설계문서_v1.20.md#FD-11.5, 정책문서 7.10-A/20.1-B

FD-10.3(패닉 프롬프트)가 위기 상황에서 참조할 목적지를 평상시에 미리
등록해둔다 — "위기 상황이 닥친 뒤에는 등록 자체가 불가능"이 실제로
강제되려면 평상시/위기상황을 구분해야 한다.

해석(FD-9 연동): FD-11.5 원문의 "FD-9가 감지한 카운터파티 리스크 심각
신호가 활성 상태"를 가리킬 별도의 영속 플래그가 FD-9 어디에도 없다 —
Circuit Breaker(FD-9.4)의 RESTRICTED 이상 레벨을 "위기 상황"으로 재사용
한다(9.6 Reconciliation이 이미 같은 인프라를 재사용하는 것과 동일 원칙 —
새 상태를 발명하지 않는다).

destination_address는 AES-256-GCM으로 암호화 저장(src/core/security/
encryption.py, 07번 §7.3 CREDENTIAL_ENCRYPTION_KEY 재사용). 삭제(revoke)
기능은 04번 원문 그대로 의도적으로 제공하지 않는다.

FD-17.1 이벤트 발행 — 등록 성공 시 "security.withdrawal_whitelist.added"를
발행한다(4.9 강제원칙 — 이 채널은 사용자가 끌 수 없다, channel_policy.py).
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
from src.core.security.encryption import decrypt, encrypt

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_CRISIS_LEVELS = {
    CircuitBreakerLevel.RESTRICTED,
    CircuitBreakerLevel.HALTED,
    CircuitBreakerLevel.EMERGENCY,
}


class WithdrawalWhitelistError(Exception):
    """FD-11.5 등록 거부 — 라우터가 409로 변환."""


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

        encrypted_address = encrypt(destination_address, self._encryption_key)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO withdrawal_whitelist (user_id, exchange, destination_address, label) "
                "VALUES ($1, $2, $3, $4) RETURNING id, exchange, label",
                user_id,
                exchange,
                encrypted_address,
                label,
            )
            # destination_address는 절대 남기지 않는다 — 실제 출금 목적지라
            # audit_log에조차 평문으로 두면 안 됨(어느 거래소에 등록했는지·
            # 라벨·결과만 기록).
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
                destination_address=decrypt(row["destination_address"], self._encryption_key),
                label=row["label"],
            )
            for row in rows
        ]

    async def fetch_for_panic_prompt(self, user_id: UUID, exchange: str) -> list[WhitelistEntry]:
        """FD-10.3 PanicPromptGenerator의 fetch_whitelist DI 콜백
        시그니처(user_id, exchange) -> list[WhitelistEntry]에 그대로 연결
        가능하도록 변환한다 — 실제 배선은 앱 조립 단계(16번)에서."""
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
