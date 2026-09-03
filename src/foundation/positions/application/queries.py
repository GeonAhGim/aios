"""LB-17 — positions 조회 전용 애플리케이션 계층(pos_snapshot/pos_nav).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-17.

읽기 전용: 이 모듈은 `SnapshotRepository`/`NavRepository`에 위임만 하고
어떤 쓰기도 하지 않는다(71번 §6). LB-1 `contracts/v1.py`의
`PositionSnapshotView`/`NAVSnapshot`을 그대로(필드명 변경 없이) 반환한다 —
프론트 파싱(task-628 decision, `frontend/packages/shared-types/src/
positionView.ts`)이 SSOT로 쓰는 필드명이 이 계약과 1:1이므로, 여기서 별도
뷰 모델로 감싸면 그 계약이 두 곳에서 따로 진화한다.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from src.foundation.positions.contracts.v1 import NAVSnapshot, PositionSnapshotView
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

__all__ = ["get_nav", "list_open_positions"]


async def list_open_positions(
    pool: asyncpg.Pool, tenant_id: UUID, account_id: UUID, *, snapshots: SnapshotRepository
) -> list[PositionSnapshotView]:
    """`quantity != 0`인 열린 포지션 전체. 없으면 빈 리스트
    (`SnapshotRepository.list_open` 계약 그대로)."""
    async with pool.acquire() as conn:
        return await snapshots.list_open(conn, tenant_id, account_id)


async def get_nav(
    pool: asyncpg.Pool, account_id: UUID, nav_date: date, *, nav_repo: NavRepository
) -> NAVSnapshot | None:
    """해당 일자 NAV. 아직 산출되지 않았으면 `None`
    (`NavRepository.get` 계약 그대로)."""
    async with pool.acquire() as conn:
        return await nav_repo.get(conn, account_id, nav_date)
