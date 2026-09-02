"""LC-8a — 원장 저널 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §5, §9 LC-8.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_journal_repository.py,
LC-8b/task-320)은 모른다(71번 §4). 이 파일 자체는 I/O를 하지 않는다 — `conn` 인자는
호출자가 이미 연 `asyncpg.Connection`을 그대로 넘긴다는 계약만 표현한다
(`src/services/oms/ports/repository.py`와 같은 패턴). `entry`는 아직 포스팅되지
않은 `LedgerEvent`(LC-1 계약)이고, 반환값 `JournalEntryView`가 실제 저장된
분개다 — 이 포트는 새 DTO를 정의하지 않는다(LC-1 재사용).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.ledger.contracts.v1 import JournalEntryView, LedgerEvent, PostingLine


@runtime_checkable
class LedgerJournalRepository(Protocol):
    async def append(
        self,
        conn: asyncpg.Connection,
        entry: LedgerEvent,
        lines: list[PostingLine],
    ) -> JournalEntryView:
        """§5 C 분개 절차: 전역 advisory lock(`hashtext('ledger_journal')`) 하에서
        다음 `sequence_no`를 배정하고 저널·행을 append한다. `idempotency_key`
        (`{event_type}:{event_ref}`)가 이미 존재하면 새로 쓰지 않고 기존
        `JournalEntryView`를 `replayed=True`로 반환한다(LEDGER_IDEMPOTENT_REPLAY,
        오류 아님) — digest가 다르면 `LEDGER_IDEMPOTENCY_DIGEST_MISMATCH`.
        호출자(`post_entry`, LC-9)가 같은 `conn`으로 잔액 갱신·감사 append를
        이어서 수행하므로, 이 메서드 자체는 커밋하지 않는다."""
        ...

    async def find_by_idempotency_key(
        self, conn: asyncpg.Connection, key: str
    ) -> JournalEntryView | None:
        """멱등 lookup. 없으면 `None` — "없음"과 오류를 구분한다."""
        ...

    async def list_since(self, conn: asyncpg.Connection, seq: int) -> list[JournalEntryView]:
        """`sequence_no > seq`인 분개를 오름차순으로 반환(무결성 검증·타임라인
        조회용). 없으면 빈 리스트."""
        ...

    async def last(self, conn: asyncpg.Connection) -> JournalEntryView | None:
        """가장 최근 분개(`sequence_no` 최댓값). 저널이 비어 있으면 `None`
        (최초 분개는 `prev_hash=None`으로 append된다)."""
        ...
