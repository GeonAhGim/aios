"""LB-11 동시 체결·재빌드 advisory lock 직렬화 검증."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.application.rebuild_snapshot import rebuild_snapshot
from tests.integration.foundation.positions.conftest import (
    force_row_replace,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    _clock,
    _command,
    _open,
    _record,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    ports as ports,
)


async def test_concurrent_rebuild_snapshot_serialised_by_advisory_lock(pool, ports):
    """F2(2) 동시 rebuild_snapshot -- 스냅샷이 저널과 어긋난(drift) 상태에서
    `rebuild_snapshot(dry_run=False)`를 동시에 2회 호출하면, 두 호출 모두
    같은 네임스페이스의 `pg_advisory_xact_lock`을 잡으려 하므로 직렬화된다:
    먼저 락을 잡은 호출이 drift를 고쳐 커밋하고, 뒤이어 락을 잡은 호출은 이미
    고쳐진 스냅샷을 다시 읽어 drift가 없다는 것을 확인하고 스킵(applied=False)
    한다. 어느 쪽도 `ConcurrencyConflictError`로 실패하지 않고, 최종
    스냅샷은 정확히 1개 행에 올바른 값만 남아야 한다(락이 없었다면
    `test_bypassing_position_lock_causes_concurrent_rebuild_conflict_gate_red`
    가 재현하는 경합으로 인해 둘 중 하나가 conflict로 죽거나 stale 값을
    덮어쓸 수 있다)."""
    tenant_id, account_id, position_key = await _open(pool)
    await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            price=Decimal("100"),
        ),
    )

    # 스냅샷을 강제로 저널과 어긋나게 만든다(변조/버그 시뮬레이션) -- drift 발생.
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("0"),
    )

    async def _rebuild():
        return await rebuild_snapshot(
            position_key,
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
            dry_run=False,
        )

    reports = await asyncio.gather(_rebuild(), _rebuild())

    applied_count = sum(1 for r in reports if r.applied)
    assert applied_count == 1, (
        f"advisory lock으로 직렬화되면 정확히 한쪽만 drift를 실제로 적용해야 한다: {reports}"
    )

    async with pool.acquire() as conn:
        snapshot_count = await conn.fetchval(
            "SELECT count(*) FROM pos_snapshot WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert snapshot_count == 1, "동시 rebuild_snapshot 이후에도 스냅샷 행은 1개여야 한다"
    assert snapshot_row["quantity"] == Decimal("10"), (
        "최종 스냅샷은 저널이 fold한 값과 일치해야 한다"
    )
    assert snapshot_row["last_journal_seq"] == 1


async def test_concurrent_record_fill_same_order_and_fill_seq_applies_once(pool, ports):
    """task-8812/task-8675 F2(L) — advisory lock(`_acquire_position_lock`,
    record_fill.py:168)이 같은 order_id+fill_seq의 동시 REPLAY 경쟁을 실제로
    직렬화하는지 확인하는 회귀망. 순차 REPLAY는 이미 커버되고(위
    `test_replay_skips_realized_pnl_recompute_and_single_upsert`,
    `test_replay_same_fill_returns_existing_without_duplicate`), 동시
    rebuild_snapshot도 이미 커버되지만(`test_concurrent_rebuild_snapshot_
    serialised_by_advisory_lock`), 같은 command를 `asyncio.gather`로 진짜
    동시에 2회 `record_fill`하는 경로는 아직 아무 테스트도 재현하지 않았다
    — 각 호출이 독립된 커넥션/트랜잭션을 쓰므로 advisory lock이 없다면 두
    트랜잭션이 동시에 `is_replay_candidate=False`를 보고 원가법을 두 번
    계산할 수 있다."""
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
    )

    results = await asyncio.gather(
        _record(pool, ports, command),
        _record(pool, ports, command),
    )

    assert {r.quantity for r in results} == {Decimal("10")}
    assert {r.last_journal_seq for r in results} == {1}
    assert {tuple(lot.quantity for lot in r.lots) for r in results} == {(Decimal("10"),)}

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        sequence_nos = await conn.fetch(
            "SELECT sequence_no FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 1
    assert [row["sequence_no"] for row in sequence_nos] == [1]
    assert snapshot_row["quantity"] == Decimal("10")
    assert snapshot_row["last_journal_seq"] == 1
    assert snapshot_row["last_journal_seq"] == 1
