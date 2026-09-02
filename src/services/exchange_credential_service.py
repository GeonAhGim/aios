"""12.2/12.3 — 거래소 자격증명 등록/해지 (ExchangeCredentialService).

Spec: 기능설계문서_v1.20.md#FD-13.3(개발명세서), 13_multi_tenancy_auth_v1.4.md#§13.3

등록 직후 FD-3.2(잔고조회) 1회 테스트 호출로 키 유효성을 검증하고, 유효
하지 않으면 저장하지 않는다. api_key/api_secret/extra는 각각 AES-256-GCM
암호화(src/core/security/encryption.py, 07번 §7.3 키 재사용) 후 BYTEA로
저장 — 평문은 검증 호출이 끝나는 즉시 폐기된다.

출금 권한 조회: 02번 문서 원칙(이 Adapter의 어떤 메서드도 출금 기능을
포함하지 않음)에 따라 BitgetAdapter/KISAdapter 둘 다 권한범위 조회
엔드포인트를 구현하지 않았다 — FD-13.3 예외상황이 명시한 대로("거래소
API가 권한범위 조회를 지원하지 않는 경우 → 경고 문구로 대체") 항상
경고를 반환한다(거짓으로 "확인됨"이라 주장하지 않는다).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.core.security.encryption import decrypt, encrypt
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.factory import UnsupportedExchangeError, build_adapter


class AdapterFactory(Protocol):
    def __call__(
        self,
        exchange: str,
        api_key: str,
        api_secret: str,
        extra: dict[str, str] | None,
        *,
        demo_mode: bool = ...,
    ) -> ExchangeAdapter: ...


_WITHDRAWAL_PERMISSION_WARNING = (
    "이 거래소는 출금 권한 자동확인을 지원하지 않습니다. "
    "반드시 출금 권한이 제외된 키인지 직접 확인해주세요."
)


class ExchangeCredentialError(Exception):
    """FD-13.3 등록/해지 실패 — 라우터가 400으로 변환."""


class CredentialSummary(BaseModel):
    id: int
    exchange: str
    is_active: bool
    linked_at: datetime
    withdrawal_permission_warning: str | None = None


class ExchangeCredentialService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        encryption_key: str,
        adapter_factory: AdapterFactory = build_adapter,
        demo_mode: bool = True,
    ) -> None:
        self._pool = pool
        self._encryption_key = encryption_key
        self._adapter_factory = adapter_factory
        self._demo_mode = demo_mode

    async def register(
        self,
        user_id: UUID,
        exchange: str,
        api_key: str,
        api_secret: str,
        extra: dict[str, str] | None = None,
    ) -> CredentialSummary:
        try:
            adapter = self._adapter_factory(
                exchange, api_key, api_secret, extra, demo_mode=self._demo_mode
            )
        except UnsupportedExchangeError as exc:
            raise ExchangeCredentialError(str(exc)) from exc

        try:
            await adapter.get_balance()
        except Exception as exc:
            raise ExchangeCredentialError(
                "자격증명이 유효하지 않습니다 — 거래소 인증에 실패했습니다."
            ) from exc
        finally:
            aclose = getattr(adapter, "aclose", None)
            if aclose is not None:
                await aclose()

        encrypted_key = encrypt(api_key, self._encryption_key).encode("ascii")
        encrypted_secret = encrypt(api_secret, self._encryption_key).encode("ascii")
        encrypted_extra = encrypt(json.dumps(extra or {}), self._encryption_key).encode("ascii")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO exchange_credentials
                    (user_id, exchange, api_key_encrypted, api_secret_encrypted, extra_encrypted)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, exchange) DO UPDATE
                    SET api_key_encrypted = EXCLUDED.api_key_encrypted,
                        api_secret_encrypted = EXCLUDED.api_secret_encrypted,
                        extra_encrypted = EXCLUDED.extra_encrypted,
                        is_active = true,
                        linked_at = now(),
                        revoked_at = NULL
                RETURNING id, exchange, is_active, linked_at
                """,
                user_id,
                exchange,
                encrypted_key,
                encrypted_secret,
                encrypted_extra,
            )
            # api_key/api_secret/extra 값은 절대 남기지 않는다 — 어느
            # 거래소에 등록했는지와 결과만.
            await record_audit_log(
                conn, actor_agent=str(user_id), action_type="exchange_credential.registered",
                user_id=user_id, decision_data={"exchange": exchange},
            )
        return CredentialSummary(
            id=row["id"],
            exchange=row["exchange"],
            is_active=row["is_active"],
            linked_at=row["linked_at"],
            withdrawal_permission_warning=_WITHDRAWAL_PERMISSION_WARNING,
        )

    async def revoke(self, user_id: UUID, exchange: str) -> None:
        """물리 삭제 아님 — revoked_at 갱신(감사 추적, 13번 §13.3)."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE exchange_credentials SET is_active = false, revoked_at = now() "
                "WHERE user_id = $1 AND exchange = $2 AND is_active = true",
                user_id,
                exchange,
            )
            if result == "UPDATE 0":
                raise ExchangeCredentialError(f"활성 상태인 {exchange} 자격증명이 없습니다.")
            await record_audit_log(
                conn, actor_agent=str(user_id), action_type="exchange_credential.revoked",
                user_id=user_id, decision_data={"exchange": exchange},
            )

    async def list_for_user(self, user_id: UUID) -> list[CredentialSummary]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, exchange, is_active, linked_at FROM exchange_credentials "
                "WHERE user_id = $1 ORDER BY linked_at DESC",
                user_id,
            )
        return [
            CredentialSummary(
                id=row["id"],
                exchange=row["exchange"],
                is_active=row["is_active"],
                linked_at=row["linked_at"],
            )
            for row in rows
        ]

    async def get_decrypted(
        self, user_id: UUID, exchange: str
    ) -> tuple[str, str, dict[str, str]] | None:
        """12.4 CredentialResolver 전용 — 복호화는 여기서만 이루어지고,
        평문은 호출부가 즉시 Adapter 생성에만 쓰고 버린다(로그 노출 금지)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT api_key_encrypted, api_secret_encrypted, extra_encrypted "
                "FROM exchange_credentials "
                "WHERE user_id = $1 AND exchange = $2 AND is_active = true",
                user_id,
                exchange,
            )
        if row is None:
            return None

        api_key = decrypt(row["api_key_encrypted"].decode("ascii"), self._encryption_key)
        api_secret = decrypt(row["api_secret_encrypted"].decode("ascii"), self._encryption_key)
        extra_raw = row["extra_encrypted"]
        extra = (
            json.loads(decrypt(extra_raw.decode("ascii"), self._encryption_key))
            if extra_raw is not None
            else {}
        )
        return api_key, api_secret, extra
