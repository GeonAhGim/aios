"""LB-13 재빌드 경합과 저장소 실패 시 스냅샷·저널 격리 검증."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.rebuild_snapshot import (
    rebuild_snapshot,
)
from tests.integration.foundation.positions.conftest import (
    force_row_replace,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    _clock,
    _fill,
    _open,
    _RealPorts,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    ports as ports,
)


async def test_bypassing_position_lock_causes_concurrent_rebuild_conflict_gate_red(
    pool: asyncpg.Pool, ports: _RealPorts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(DEEPEN task-2958) + 동시성 증명 — 모듈독스트링이
    전제하는 `_acquire_position_lock`(pg_advisory_xact_lock, record_fill과
    같은 네임스페이스)이 없다면, rebuild_snapshot이 스냅샷을 읽은 *직후*
    다른 트랜잭션(record_fill)이 같은 포지션에 새 체결을 커밋해도 이
    재빌드는 그 사실을 모른 채 stale한 snapshot 기준으로 drift를 계산하고
    upsert를 시도한다 — 105번 표준(조건부 UPDATE, expected_seq)이 실제로
    0행을 반환해 `ConcurrencyConflictError`로 적색이 됨을 재현한다(락이
    있었다면 이 두 번째 커밋은 재빌드의 advisory lock이 풀릴 때까지
    대기했을 것이므로 이 경합 자체가 없다 — I-10 "우회불가"의 증거).
    `pos_journal`은 rebuild_snapshot이 절대 쓰지 않으므로(WORM), 실패한
    재빌드 시도는 경합에서 이긴 record_fill의 엔트리 1건 외에 아무 흔적도
    남기지 않는다 -- 스냅샷도 그 record_fill이 이미 올바르게 반영한 값
    그대로이지 rebuild가 되돌리거나 손대지 않는다."""
    import src.foundation.positions.application.rebuild_snapshot as rebuild_snapshot_module
    from src.core.db.conditional_write import ConcurrencyConflictError

    tenant_id, account_id, position_key = await _open(pool)

    async def _noop_lock(conn: object, key: str) -> None:
        return None

    monkeypatch.setattr(rebuild_snapshot_module, "_acquire_position_lock", _noop_lock)

    real_get = PostgresSnapshotRepository.get
    raced = False

    async def _get_then_race(
        self: PostgresSnapshotRepository, conn: Any, tenant_id_: UUID, position_key_: str
    ) -> Any:
        nonlocal raced
        snapshot = await real_get(self, conn, tenant_id_, position_key_)
        if not raced:
            raced = True
            await _fill(
                pool,
                ports,
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                fill_seq=1,
                order_id=uuid4(),
            )
        return snapshot

    monkeypatch.setattr(PostgresSnapshotRepository, "get", _get_then_race)

    with pytest.raises(ConcurrencyConflictError):
        await rebuild_snapshot(
            position_key,
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
            dry_run=False,
        )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 1, "rebuild_snapshot은 WORM 저널에 절대 쓰지 않는다"
    assert snapshot_row["quantity"] == Decimal("1"), (
        "경합에서 이긴 record_fill이 반영한 값이 실패한 재빌드로 손상되면 안 된다"
    )
    assert snapshot_row["last_journal_seq"] == 1


# --- DEEPEN additions: negative / failure-injection tests ---


async def test_rebuild_snapshot_journal_list_failure(
    pool: asyncpg.Pool, ports: _RealPorts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입(DEEPEN) — journal.list_for 가 DB 예외를 던지면 rebuild_snapshot
    은 트랜잭션 롤백으로 전체 연산을 취소해야 한다. 저널 행 수가 늘지 않고
    스냅샷도 변하지 않아야 한다."""

    tenant_id, account_id, position_key = await _open(pool)
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("5"),
        price=Decimal("50"),
        fill_seq=1,
        order_id=uuid4(),
    )

    # Capture pre-state
    async with pool.acquire() as conn:
        journal_before = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        qty_before = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )

    # Monkeypatch journal.list_for to raise
    async def _raise_list_for(conn: object, position_key_: str) -> None:
        raise asyncpg.PostgresError("simulated connection reset")

    monkeypatch.setattr(ports.journal, "list_for", _raise_list_for)

    with pytest.raises(asyncpg.PostgresError):
        await rebuild_snapshot(
            position_key,
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
            dry_run=False,
        )

    # Verify no side effects — WORM journal untouched, snapshot unchanged
    async with pool.acquire() as conn:
        journal_after = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        qty_after = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert journal_after == journal_before, "DB 예외 발생 시 저널 행 수가 늘어나면 안 된다"
    assert qty_after == qty_before, "DB 예외 발생 시 스냅샷이 변하면 안 된다"


async def test_rebuild_snapshot_upsert_failure_isolation(
    pool: asyncpg.Pool, ports: _RealPorts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입(DEEPEN) — fold 계산은 성공했지만 snapshots.upsert 가 예외를
    던지는 경우. 트랜잭션이 롤백되어 저널/스냅샷 모두 원상태여야 한다.
    rebuild_snapshot 자체는 예외를 전파한다."""

    tenant_id, account_id, position_key = await _open(pool)
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("3"),
        price=Decimal("200"),
        fill_seq=1,
        order_id=uuid4(),
    )

    # Corrupt snapshot so drift exists (triggers upsert path)
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("0"),
    )

    # Capture pre-state
    async with pool.acquire() as conn:
        qty_before = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )
        seq_before = await conn.fetchval(
            "SELECT last_journal_seq FROM pos_snapshot WHERE position_key = $1", position_key
        )

    # Monkeypatch upsert to raise
    async def _raise_upsert(conn: object, snapshot: object, expected_seq: object) -> None:
        raise asyncpg.IntegrityConstraintViolationError(
            'duplicate key value violates constraint "pos_snapshot_pkey"'
        )

    monkeypatch.setattr(ports.snapshots, "upsert", _raise_upsert)

    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await rebuild_snapshot(
            position_key,
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
            dry_run=False,
        )

    # Verify rollback: snapshot unchanged despite upsert failure
    async with pool.acquire() as conn:
        qty_after = await conn.fetchval(
            "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
        )
        seq_after = await conn.fetchval(
            "SELECT last_journal_seq FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert qty_after == qty_before, "upsert 실패 시 스냅샷 quantity 가 변하면 안 된다"
    assert seq_after == seq_before, "upsert 실패 시 last_journal_seq 가 변하면 안 된다"
