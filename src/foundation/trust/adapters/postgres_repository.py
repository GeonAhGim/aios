"""TrustRepository의 asyncpg 구현.

Spec: AIOSproject 73번 §2.1/§7, 105번(동시성 표준) — revoke_consent()는
conditional_update()를 통해서만 상태를 바꾼다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure


def _row_to_disclosure(row: asyncpg.Record) -> Disclosure:
    return Disclosure(
        id=row["id"],
        purpose=row["purpose"],
        revision=row["revision"],
        content_hash=row["content_hash"],
        published_at=row["published_at"],
        retired_at=row["retired_at"],
    )


def _row_to_consent(row: asyncpg.Record) -> Consent:
    return Consent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        subject_id=row["subject_id"],
        purpose=row["purpose"],
        disclosure_id=row["disclosure_id"],
        disclosure_revision=row["disclosure_revision"],
        state=ConsentState(row["state"]),
        accepted_at=row["accepted_at"],
        revoked_at=row["revoked_at"],
        expires_at=row["expires_at"],
    )


class PostgresTrustRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM disclosure WHERE purpose = $1 AND retired_at IS NULL "
                "ORDER BY revision DESC LIMIT 1",
                purpose,
            )
        return _row_to_disclosure(row) if row is not None else None

    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> Disclosure | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM disclosure WHERE purpose = $1 AND revision = $2", purpose, revision
            )
        return _row_to_disclosure(row) if row is not None else None

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM consent_record WHERE tenant_id = $1 AND purpose = $2 "
                "AND state = 'ACTIVE'",
                tenant_id,
                purpose,
            )
        return _row_to_consent(row) if row is not None else None

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM consent_record WHERE tenant_id = $1 AND purpose = $2 "
                "ORDER BY accepted_at DESC LIMIT 1",
                tenant_id,
                purpose,
            )
        return _row_to_consent(row) if row is not None else None

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM consent_record WHERE tenant_id = $1 AND state = 'ACTIVE' "
                "ORDER BY accepted_at DESC",
                tenant_id,
            )
        return [_row_to_consent(row) for row in rows]

    async def insert_consent(
        self,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        purpose: str,
        disclosure_id: UUID,
        disclosure_revision: int,
        expires_at: datetime | None,
    ) -> Consent:
        # uq_consent_record_active_purpose 부분 unique index(84b7d0faf14f)가 같은
        # tenant/purpose에 ACTIVE 레코드가 둘 이상 생기는 걸 DB 레벨에서 막는다 —
        # 이게 이 append-only insert 경로의 동시성 방어(105번 §2.2 예외 기준 중
        # "단일 소유자가 스키마 레벨 UNIQUE 제약으로 보장되는 경우"에 해당).
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO consent_record "
                    "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision, "
                    " expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                    tenant_id,
                    subject_id,
                    purpose,
                    disclosure_id,
                    disclosure_revision,
                    expires_at,
                )
            except asyncpg.UniqueViolationError as exc:
                # uq_consent_record_active_purpose 위반 — 이 요청이 존재 여부를
                # 확인한 시점(get_active_consent) 이후, INSERT 시점 사이에 다른
                # 요청이 먼저 같은 tenant/purpose에 ACTIVE consent를 만들었다.
                # 105번 §2.2가 인정하는 "스키마 UNIQUE 제약이 단일 소유자를
                # 보장" 케이스의 경합 신호를 표준 예외로 번역한다.
                raise ConcurrencyConflictError(
                    f"consent_record: tenant_id={tenant_id} purpose={purpose}에 대한 ACTIVE "
                    "동의가 그 사이 다른 요청으로 먼저 생성됐습니다(동시 처리 충돌)."
                ) from exc
        return _row_to_consent(row)

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        async with self._pool.acquire() as conn:
            pre_check = await conn.fetchrow(
                "SELECT tenant_id FROM consent_record WHERE id = $1", consent_id
            )
            if pre_check is None:
                raise LookupError(f"존재하지 않는 동의입니다: {consent_id}")
            if pre_check["tenant_id"] != tenant_id:
                raise PermissionError("다른 tenant의 동의는 철회할 수 없습니다.")

            row = await conditional_update(
                conn,
                table="consent_record",
                id_column="id",
                id_value=consent_id,
                expected_state_column="state",
                expected_state_value=ConsentState.ACTIVE.value,
                set_values={
                    "state": ConsentState.REVOKED.value,
                    "revoked_at": datetime.now(timezone.utc),
                },
            )
        return _row_to_consent(row)
