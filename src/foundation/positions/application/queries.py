"""LB-17 — positions 조회 전용 애플리케이션 계층(pos_snapshot/pos_journal/pos_nav).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-17, LB-19.

읽기 전용: 이 모듈은 `SnapshotRepository`/`PositionJournalRepository`/
`NavRepository`에 위임만 하고 어떤 쓰기도 하지 않는다(71번 §6). LB-1
`contracts/v1.py`의 `PositionSnapshotView`/`PositionJournalEntryView`/
`NAVSnapshot`을 그대로(필드명 변경 없이) 반환한다 — 프론트 파싱(task-628
decision, `frontend/packages/shared-types/src/positionView.ts`)이 SSOT로
쓰는 필드명이 이 계약과 1:1이므로, 여기서 별도 뷰 모델로 감싸면 그 계약이
두 곳에서 따로 진화한다.

테넌트 스코프(LB-19, PLT §3): `pos_journal.list_for`·`pos_nav_daily.get`은
포트 계약상 tenant를 받지 않으므로, 이 계층이 먼저 소유를 확인한다 —
스냅샷은 `SnapshotRepository.get(tenant_id, key)`(LB-18 cross_tenant 수정),
계정은 `pos_account.tenant_id`로. 없음과 타 테넌트 소유를 구분하지 않고 같은
예외를 던진다(존재 비노출, 404 동형). `pos_account` 조회 포트는 §2 표에
없어 여기서 SQL 한 줄로 읽는다 — 계정 저장소 포트가 생기면 그리로 옮긴다.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import asyncpg

from src.foundation.positions.contracts.v1 import (
    NAVSnapshot,
    PositionJournalEntryView,
    PositionSnapshotView,
)
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

__all__ = [
    "MAX_NAV_RANGE_DAYS",
    "NavRangeInvalidError",
    "PositionAccountNotFoundError",
    "PositionNotFoundError",
    "get_nav",
    "list_journal",
    "list_nav_range",
    "list_open_positions",
    "list_positions",
]

# 일별 NAV 체인 한 번의 조회 상한(윤년 포함 1년). 하루 한 행이라 366회
# `get` 왕복이 최악 — 그 이상은 클라이언트가 범위를 나눠 부른다.
MAX_NAV_RANGE_DAYS = 366


class PositionNotFoundError(Exception):
    """`position_key`가 없거나 다른 tenant 소유 — 구분하지 않는다(존재 비노출)."""


class PositionAccountNotFoundError(Exception):
    """`account_id`가 없거나 다른 tenant 소유 — 구분하지 않는다(존재 비노출)."""


class NavRangeInvalidError(ValueError):
    """`end_date < start_date`이거나 범위가 `MAX_NAV_RANGE_DAYS`를 넘는다."""


async def _owned_account_ids(
    conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID | None
) -> list[UUID]:
    """tenant 소유 `pos_account` id 목록. `account_id`를 주면 그 하나만(소유가
    아니면 빈 리스트 — 호출자가 not-found로 번역)."""
    if account_id is None:
        rows = await conn.fetch(
            "SELECT account_id FROM pos_account WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id,
        )
    else:
        rows = await conn.fetch(
            "SELECT account_id FROM pos_account WHERE tenant_id = $1 AND account_id = $2",
            tenant_id,
            account_id,
        )
    return [row["account_id"] for row in rows]


async def list_open_positions(
    pool: asyncpg.Pool, tenant_id: UUID, account_id: UUID, *, snapshots: SnapshotRepository
) -> list[PositionSnapshotView]:
    """`quantity != 0`인 열린 포지션 전체. 없으면 빈 리스트
    (`SnapshotRepository.list_open` 계약 그대로)."""
    async with pool.acquire() as conn:
        return await snapshots.list_open(conn, tenant_id, account_id)


async def list_positions(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    account_id: UUID | None,
    instrument_id: UUID | None,
    snapshots: SnapshotRepository,
) -> list[PositionSnapshotView]:
    """LB-19 `GET /positions` — tenant의 열린 포지션(계정·instrument 필터).
    `account_id`가 tenant 소유가 아니면 `PositionAccountNotFoundError`;
    필터 결과가 없는 것은 오류가 아니라 빈 리스트다."""
    async with pool.acquire() as conn:
        accounts = await _owned_account_ids(conn, tenant_id, account_id)
        if account_id is not None and not accounts:
            raise PositionAccountNotFoundError(f"계정을 찾을 수 없습니다: {account_id}")
        views: list[PositionSnapshotView] = []
        for owned in accounts:
            views.extend(await snapshots.list_open(conn, tenant_id, owned))
    if instrument_id is not None:
        views = [view for view in views if view.instrument_id == instrument_id]
    return sorted(views, key=lambda view: view.position_key)


async def list_journal(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    position_key: str,
    *,
    after_seq: int,
    limit: int,
    snapshots: SnapshotRepository,
    journal: PositionJournalRepository,
) -> tuple[list[PositionJournalEntryView], int | None]:
    """LB-19 `GET /positions/{key}/journal` — `sequence_no > after_seq`부터
    최대 `limit`건과, 더 있으면 다음 커서(마지막 `sequence_no`), 없으면 None.
    스냅샷 소유 확인이 먼저다(저널 포트는 tenant를 모른다)."""
    if limit < 1:
        raise ValueError("limit는 1 이상이어야 합니다.")
    async with pool.acquire() as conn:
        if await snapshots.get(conn, tenant_id, position_key) is None:
            raise PositionNotFoundError(f"포지션을 찾을 수 없습니다: {position_key}")
        entries = await journal.list_for(conn, position_key, from_seq=after_seq)
    page = entries[:limit]
    next_seq = page[-1].sequence_no if len(entries) > limit else None
    return page, next_seq


async def get_nav(
    pool: asyncpg.Pool, account_id: UUID, nav_date: date, *, nav_repo: NavRepository
) -> NAVSnapshot | None:
    """해당 일자 NAV. 아직 산출되지 않았으면 `None`
    (`NavRepository.get` 계약 그대로)."""
    async with pool.acquire() as conn:
        return await nav_repo.get(conn, account_id, nav_date)


async def list_nav_range(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    account_id: UUID,
    *,
    start_date: date,
    end_date: date,
    nav_repo: NavRepository,
) -> list[NAVSnapshot]:
    """LB-19 `GET /positions/nav` — `[start_date, end_date]` 일별 NAV 체인을
    오름차순으로. 아직 산출되지 않은 날은 행이 없다(0으로 채우지 않는다 —
    호출자가 빠진 날짜를 그대로 드러낸다)."""
    if end_date < start_date:
        raise NavRangeInvalidError("end_date가 start_date보다 앞섭니다.")
    if (end_date - start_date).days + 1 > MAX_NAV_RANGE_DAYS:
        raise NavRangeInvalidError(f"조회 범위는 최대 {MAX_NAV_RANGE_DAYS}일입니다.")
    async with pool.acquire() as conn:
        if not await _owned_account_ids(conn, tenant_id, account_id):
            raise PositionAccountNotFoundError(f"계정을 찾을 수 없습니다: {account_id}")
        series: list[NAVSnapshot] = []
        day = start_date
        while day <= end_date:
            nav = await nav_repo.get(conn, account_id, day)
            if nav is not None:
                series.append(nav)
            day += timedelta(days=1)
    return series
