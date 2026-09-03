"""LB-7 — 포지션 스냅샷 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §4.3, §9 LB-7.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_snapshot_repository.py,
LB-9)은 모른다(71번 §4). `upsert`는 `conditional_update`(기대 `last_journal_seq`)로
동작한다는 계약만 표현한다 — 실제 SQL·잠금은 어댑터의 책임이다.

`get`은 `tenant_id`로 스코프한다(task-489/LB-18 cross_tenant 적대적 테스트가 드러낸
실결함의 수정 — 이전에는 `position_key`만으로 조회해 다른 tenant의 `position_key`를
알아내면 그 스냅샷을 그대로 읽을 수 있었다). 소유자가 다르면 존재하지 않는 것과
구분 없이 `None`을 돌려준다 — 남의 포지션 존재 자체를 흘리지 않는 쪽이 안전하다."""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.positions.contracts.v1 import PositionSnapshotView


@runtime_checkable
class SnapshotRepository(Protocol):
    async def get(
        self, conn: asyncpg.Connection, tenant_id: UUID, position_key: str
    ) -> PositionSnapshotView | None:
        """스냅샷이 아직 없거나(첫 체결 전), `position_key`는 존재하지만 소유
        `tenant_id`가 다르면 `None`(구분하지 않는다 — 존재 비노출)."""
        ...

    async def upsert(
        self, conn: asyncpg.Connection, snapshot: PositionSnapshotView, expected_seq: int
    ) -> PositionSnapshotView:
        """§4.3 "스냅샷 = fold(저널)" 결과를 조건부로 반영한다. 기존 행의
        `last_journal_seq != expected_seq`면 `ConcurrencyConflictError` —
        호출자가 같은 `conn`에서 저널 append 직후 재조회 없이 넘긴 값이
        어긋났다는 뜻이다. 최초 upsert는 `expected_seq=0`."""
        ...

    async def list_open(
        self, conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID
    ) -> list[PositionSnapshotView]:
        """`quantity != 0`인 스냅샷 전체(조회 리프 `application/queries.py` 소비).
        없으면 빈 리스트."""
        ...
