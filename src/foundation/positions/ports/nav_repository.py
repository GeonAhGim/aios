"""LB-7 — 일별 NAV 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §4.3, §9 LB-7.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_nav_repository.py,
LB-9)은 모른다(71번 §4). `pos_nav_daily`는 WORM(§9 LB-8) — `insert`만 있고
update는 없다.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.positions.contracts.v1 import NAVSnapshot


@runtime_checkable
class NavRepository(Protocol):
    async def insert(self, conn: asyncpg.Connection, nav: NAVSnapshot) -> NAVSnapshot:
        """`(account_id, nav_date)` UNIQUE 위반이면 어댑터가 예외를 던진다 —
        같은 날 재계산은 호출자(`compute_daily_nav`, LB-15)가 먼저 `get`으로
        멱등 여부를 판단해야 한다."""
        ...

    async def get(
        self, conn: asyncpg.Connection, account_id: UUID, nav_date: date
    ) -> NAVSnapshot | None:
        """해당 일자 NAV가 아직 없으면 `None`."""
        ...
