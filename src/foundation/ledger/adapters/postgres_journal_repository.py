"""LC-8b — `LedgerJournalRepository`(ports/journal_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §5, §9 LC-8.

`append()`가 이 파일의 핵심이다. 해시 체인은 "이전 엔트리를 읽고, 그것과
연결된 새 해시를 계산해서 insert"라는 read-then-write라 `conditional_update`가
아니라 `pg_advisory_xact_lock`으로 전역 append를 직렬화한다
(`evidence/adapters/postgres_repository.py`의 tenant별 lock과 같은 기법,
다만 `sequence_no`가 테넌트 없는 전역 카운터라 단일 키만 쓴다).

`ledger_journal_entry.audit_event_id`가 `NOT NULL REFERENCES
foundation_audit_event(id)`이므로, 저널 엔트리를 insert하기 전에 같은
트랜잭션(`conn`) 안에서 FND-03 감사 이벤트를 먼저 만든다 — 이게 이 리프의
책임이다(LC-9 `post_entry`가 별도로 감사 이벤트를 또 남긴다면 그건
커맨드 단위 상위 감사이고, 이건 저널 엔트리 자체의 FK 제약을 만족시키기
위한 필수 링크다).

멱등 재전송 판정: `LedgerEvent`의 `amount`/`parties`/`extra`는 어느
저널 테이블에도 저장되지 않는다(스키마는 posting line만 보존) — 그래서
`domain/idempotency.event_digest`(LedgerEvent 전체 필드 대상)가 아니라
`domain/hash_chain.lines_digest(lines)`를 저장된 `entry.lines_digest`와
비교해 재전송 판정한다. 같은 `idempotency_key`로 다른 내용의 `lines`가
들어오면 `IdempotencyDigestMismatchError`(재시도 불가).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.ledger.adapters.postgres_balance_repository import UnknownAccountError
from src.foundation.ledger.contracts.v1 import (
    JournalEntryView,
    LedgerEvent,
    LedgerEventType,
    PostingLine,
    Side,
)
from src.foundation.ledger.domain.hash_chain import entry_hash, lines_digest
from src.foundation.ledger.domain.idempotency import assert_same_digest, idempotency_key

_LOCK_KEY = "ledger_journal"


def _row_to_view(
    row: asyncpg.Record, lines: list[PostingLine], *, replayed: bool
) -> JournalEntryView:
    return JournalEntryView(
        entry_id=row["entry_id"],
        sequence_no=row["sequence_no"],
        event_type=LedgerEventType(row["event_type"]),
        event_ref=row["event_ref"],
        idempotency_key=row["idempotency_key"],
        lines=lines,
        lines_digest=row["lines_digest"],
        prev_hash=row["prev_hash"],
        entry_hash=row["entry_hash"],
        audit_event_id=row["audit_event_id"],
        posted_at=row["posted_at"],
        replayed=replayed,
    )


class PostgresJournalRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._audit_repo = PostgresAuditEventRepository(pool)

    async def append(
        self,
        conn: asyncpg.Connection,
        entry: LedgerEvent,
        lines: list[PostingLine],
    ) -> JournalEntryView:
        key = idempotency_key(entry)
        new_digest = lines_digest(lines)

        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _LOCK_KEY)

        existing = await self.find_by_idempotency_key(conn, key)
        if existing is not None:
            assert_same_digest(key, existing.lines_digest, new_digest)
            return existing.model_copy(update={"replayed": True})

        last_row = await conn.fetchrow(
            "SELECT sequence_no, entry_hash FROM ledger_journal_entry "
            "ORDER BY sequence_no DESC LIMIT 1"
        )
        next_seq = 1 if last_row is None else last_row["sequence_no"] + 1
        prev_hash: str | None = None if last_row is None else last_row["entry_hash"]

        posted_at = datetime.now(timezone.utc)
        new_hash = entry_hash(
            prev_hash, next_seq, entry.event_type, entry.event_ref, new_digest, posted_at
        )

        entry_id = uuid4()
        account_ids = await self._resolve_account_ids(conn, {line.account_code for line in lines})

        payload: dict[str, object] = {
            "event_ref": entry.event_ref,
            "amount": str(entry.amount),
            "currency": entry.currency.value,
            "line_count": len(lines),
        }
        assert_safe_payload(payload)
        audit_event = await self._audit_repo.append_event_in(
            conn,
            tenant_id=entry.tenant_id,
            aggregate_type="ledger_journal_entry",
            aggregate_id=entry_id,
            aggregate_revision=None,
            action=entry.event_type.value,
            outcome=Outcome.SUCCESS,
            actor_subject_id=entry.actor_subject_id,
            trace_id=entry.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

        row = await conn.fetchrow(
            "INSERT INTO ledger_journal_entry "
            "(entry_id, sequence_no, event_type, event_ref, idempotency_key, "
            " lines_digest, prev_hash, entry_hash, audit_event_id, posted_by, posted_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
            "RETURNING *",
            entry_id,
            next_seq,
            entry.event_type.value,
            entry.event_ref,
            key,
            new_digest,
            prev_hash,
            new_hash,
            audit_event.id,
            entry.actor_subject_id,
            posted_at,
        )

        for line in lines:
            await conn.execute(
                "INSERT INTO ledger_posting_line "
                "(entry_id, line_no, account_id, side, amount, currency) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                entry_id,
                line.line_no,
                account_ids[line.account_code],
                line.side.value,
                line.amount,
                line.currency.value,
            )

        return _row_to_view(row, lines, replayed=False)

    async def find_by_idempotency_key(
        self, conn: asyncpg.Connection, key: str
    ) -> JournalEntryView | None:
        row = await conn.fetchrow(
            "SELECT * FROM ledger_journal_entry WHERE idempotency_key = $1", key
        )
        if row is None:
            return None
        lines = await self._fetch_lines(conn, row["entry_id"])
        return _row_to_view(row, lines, replayed=False)

    async def list_since(self, conn: asyncpg.Connection, seq: int) -> list[JournalEntryView]:
        rows = await conn.fetch(
            "SELECT * FROM ledger_journal_entry WHERE sequence_no > $1 ORDER BY sequence_no ASC",
            seq,
        )
        views = []
        for row in rows:
            lines = await self._fetch_lines(conn, row["entry_id"])
            views.append(_row_to_view(row, lines, replayed=False))
        return views

    async def last(self, conn: asyncpg.Connection) -> JournalEntryView | None:
        row = await conn.fetchrow(
            "SELECT * FROM ledger_journal_entry ORDER BY sequence_no DESC LIMIT 1"
        )
        if row is None:
            return None
        lines = await self._fetch_lines(conn, row["entry_id"])
        return _row_to_view(row, lines, replayed=False)

    async def _fetch_lines(self, conn: asyncpg.Connection, entry_id: UUID) -> list[PostingLine]:
        rows = await conn.fetch(
            "SELECT pl.line_no, la.account_code, pl.side, pl.amount, pl.currency "
            "FROM ledger_posting_line pl "
            "JOIN ledger_account la ON la.account_id = pl.account_id "
            "WHERE pl.entry_id = $1 "
            "ORDER BY pl.line_no",
            entry_id,
        )
        return [
            PostingLine(
                line_no=row["line_no"],
                account_code=row["account_code"],
                side=Side(row["side"]),
                amount=row["amount"],
                currency=Currency(row["currency"]),
            )
            for row in rows
        ]

    async def _resolve_account_ids(
        self, conn: asyncpg.Connection, codes: set[str]
    ) -> dict[str, UUID]:
        rows = await conn.fetch(
            "SELECT account_id, account_code FROM ledger_account "
            "WHERE account_code = ANY($1::text[])",
            list(codes),
        )
        found = {row["account_code"]: row["account_id"] for row in rows}
        missing = codes - found.keys()
        if missing:
            raise UnknownAccountError(sorted(missing))
        return found
