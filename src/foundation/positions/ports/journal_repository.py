"""LB-7 — 포지션 저널 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §4.3, §9 LB-7.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_journal_repository.py,
LB-9)은 모른다(71번 §4). 이 파일 자체는 I/O를 하지 않는다 — `conn` 인자는 호출자가
이미 연 `asyncpg.Connection`을 그대로 넘긴다는 계약만 표현한다(LC-8a
`src/foundation/ledger/ports/journal_repository.py`와 같은 패턴). `append`는
아직 배정되지 않은 `sequence_no`/`prev_hash`/`entry_hash`/`id`/`recorded_at`를
어댑터가 advisory lock(`position_key` 단위) 하에서 채워 넣는다는 전제이므로,
그 필드들은 입력이 아니라 반환되는 `PositionJournalEntryView`에만 나타난다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Money
from src.foundation.positions.contracts.v1 import JournalEntryType, PositionJournalEntryView


@runtime_checkable
class PositionJournalRepository(Protocol):
    async def append(
        self,
        conn: asyncpg.Connection,
        *,
        position_key: str,
        entry_type: JournalEntryType,
        qty_delta: Decimal,
        price: Money | None,
        fee: Money | None,
        realized_pnl_base: Decimal,
        fx_rate: Decimal | None,
        fx_source: str | None,
        source_event_type: str,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: AwareDatetime,
    ) -> PositionJournalEntryView:
        """§4.3 저널 append. `idempotency_key`가 이미 존재하면 새로 쓰지 않고
        기존 뷰를 반환한다(POS_IDEMPOTENT_REPLAY, 오류 아님) — digest가 다르면
        POS_IDEMPOTENCY_DIGEST_MISMATCH. 커밋은 호출자(같은 `conn`으로 스냅샷
        갱신을 이어가는 application 리프)의 책임이다."""
        ...

    async def list_for(
        self, conn: asyncpg.Connection, position_key: str, from_seq: int = 0
    ) -> list[PositionJournalEntryView]:
        """`sequence_no > from_seq`인 항목을 오름차순으로 반환(재빌드·무결성
        검증용). 없으면 빈 리스트."""
        ...

    async def last(
        self, conn: asyncpg.Connection, position_key: str
    ) -> PositionJournalEntryView | None:
        """가장 최근 항목(`sequence_no` 최댓값). 저널이 비어 있으면 `None`
        (최초 항목은 `prev_hash=None`)."""
        ...
