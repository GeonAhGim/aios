"""LB-13 — 저널 전체 fold로 스냅샷을 재구축하는 운영 도구(application/rebuild_snapshot).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9 LB-13,
§4.3 "스냅샷 = fold(저널)" 불변.

`record_fill`/`record_funding_fee`(LB-11/13)와 달리 이미 열린 트랜잭션
`conn`을 받지 않는다 — 이 함수 자체가 하나의 독립된 운영 작업(스펙 §9
표의 시그니처가 `conn` 대신 `pool`을 받는 이유)이라 자기 트랜잭션을 연다.
`position_key` advisory lock([[record_fill]]과 같은 네임스페이스)을 잡아
그 사이 `record_fill`/`record_funding_fee`가 같은 포지션에 새 엔트리를
append하지 못하게 막은 채로 읽는다 — 그래야 "저널 전체를 읽는 시점"과
"fold 결과를 쓰는 시점" 사이에 락이 없어 생기는 경쟁(그 사이 도착한 새
엔트리가 재빌드 결과에 반영되지 않거나, 반대로 재빌드가 새 엔트리를
덮어쓰는 것)이 없다.

`pos_journal`은 절대 쓰지 않는다(WORM) — 이 리프가 만지는 테이블은
`pos_snapshot` 하나뿐이고, 그마저도 `snapshots.upsert`의 조건부
UPDATE(`expected_seq`)로만 쓴다. `dry_run=True`(기본값)면 drift만 보고하고
쓰지 않는다 — 운영자가 먼저 dry-run으로 drift를 확인한 뒤에만 실제로
반영하라는 §9 DoD("재빌드 drift ∅") 의도를 그대로 따른다. drift가 없으면
`dry_run=False`라도 쓰지 않는다(불필요한 쓰기·`updated_at` 갱신을 피한다).

`asset_class`는 [[record_fill]]과 같은 이유로 인자다 — `pos_snapshot`에
저장할 곳이 없어 호출자가 넘겨야 한다(스펙 §9 표의 축약 시그니처에는
없지만, `record_fill`이 이미 같은 이유로 벗어난 전례를 따른다).

원가법 재적용·펀딩/수수료 누적 규칙은 재구현하지 않는다 —
`snapshot_builder.fold`(LB-5, `functools.reduce(apply_one, ...)`)가 유일한
"진실 계산" 경로다.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import asyncpg

from src.data.models.base import AssetClass, Money
from src.foundation.positions.contracts.v1 import PositionSnapshotView, RebuildReport
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, fold
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`POS_ACCOUNT_UNKNOWN` — `position_key`에 대응하는 `pos_snapshot` 행이
    없다. 저널만으로는 `tenant_id`/`account_id`/`instrument_id`/`base_currency`
    같은 계좌 정적 컨텍스트를 복원할 수 없으므로([[snapshot_builder.
    SnapshotFold]] docstring) 재빌드도 기존 스냅샷 행을 전제한다."""

    def __init__(self, position_key: str) -> None:
        super().__init__(f"알 수 없는 position_key(스냅샷 없음): {position_key!r}")
        self.position_key = position_key


async def _acquire_position_lock(conn: asyncpg.Connection, position_key: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
        _LOCK_NAMESPACE,
        position_key,
    )


def _drift(
    current: PositionSnapshotView, folded: SnapshotFold
) -> dict[str, tuple[Decimal, Decimal]]:
    candidates: dict[str, tuple[Decimal, Decimal]] = {
        "quantity": (current.quantity, folded.quantity),
        "avg_cost": (current.avg_cost.amount, folded.avg_cost),
        "realized_pnl_base": (current.realized_pnl_base, folded.realized_pnl_base),
        "fees_base": (current.fees_base, folded.fees_base),
        "funding_base": (current.funding_base, folded.funding_base),
    }
    return {name: pair for name, pair in candidates.items() if pair[0] != pair[1]}


async def rebuild_snapshot(
    position_key: str,
    *,
    asset_class: AssetClass,
    journal: PositionJournalRepository,
    snapshots: SnapshotRepository,
    pool: asyncpg.Pool,
    clock: Clock,
    dry_run: bool = True,
) -> RebuildReport:
    async with pool.acquire() as conn, conn.transaction():
        await _acquire_position_lock(conn, position_key)

        snapshot = await snapshots.get(conn, position_key)
        if snapshot is None:
            raise UnknownPositionError(position_key)

        entries = await journal.list_for(conn, position_key)
        folded = fold(
            entries,
            position_key=position_key,
            cost_method=snapshot.cost_method,
            asset_class=asset_class,
        )
        drift = _drift(snapshot, folded)

        if dry_run or not drift:
            return RebuildReport(
                position_key=position_key, entries=len(entries), drift=drift, applied=False
            )

        rebuilt = snapshot.model_copy(
            update={
                "quantity": folded.quantity,
                "avg_cost": Money(amount=folded.avg_cost, currency=snapshot.avg_cost.currency),
                "lots": list(folded.lots),
                "realized_pnl_base": folded.realized_pnl_base,
                "fees_base": folded.fees_base,
                "funding_base": folded.funding_base,
                "last_journal_seq": folded.last_journal_seq,
                "updated_at": clock(),
            }
        )
        await snapshots.upsert(conn, rebuilt, expected_seq=snapshot.last_journal_seq)

        return RebuildReport(
            position_key=position_key, entries=len(entries), drift=drift, applied=True
        )
