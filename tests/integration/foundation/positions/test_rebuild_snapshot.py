"""LB-13 `rebuild_snapshot` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9.3 LB-13.
DoD: "재빌드 drift ∅" — 정상 스냅샷은 dry-run이든 아니든 drift가 비어야
하고, 스냅샷이 저널과 어긋나면(변조·버그) 재빌드가 그 차이를 drift로
보고하고 `dry_run=False`일 때만 실제로 고친다. `pos_journal`은 이 리프가
절대 건드리지 않는다(WORM) — 아래 테스트는 재빌드 전후로 저널 행 수가
그대로임을 확인해 이를 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.rebuild_snapshot import (
    UnknownPositionError,
    rebuild_snapshot,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.application.record_funding_fee import record_funding_fee
from src.foundation.positions.contracts.v1 import RecordFillCommand, RecordFundingCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    create_pos_account,
    force_row_replace,
    open_position,
)

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
        )
    )


class _RealPorts:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.snapshots = PostgresSnapshotRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool: asyncpg.Pool) -> _RealPorts:
    return _RealPorts(pool)


async def _open(pool: asyncpg.Pool) -> tuple[UUID, UUID, str]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _fill(
    pool: asyncpg.Pool,
    ports: _RealPorts,
    *,
    tenant_id: UUID,
    account_id: UUID,
    position_key: str,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    fill_seq: int,
    order_id: UUID,
) -> object:
    async with pool.acquire() as conn, conn.transaction():
        return await record_fill(
            conn,
            RecordFillCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=order_id,
                fill_seq=fill_seq,
                side=side,
                quantity=quantity,
                price=Money(amount=price, currency=Currency.KRW),
                fee=None,
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=ports.audit,
            clock=_clock,
        )


async def _funding(
    pool: asyncpg.Pool,
    ports: _RealPorts,
    *,
    tenant_id: UUID,
    account_id: UUID,
    position_key: str,
    amount: Decimal,
    funding_id: str,
) -> object:
    async with pool.acquire() as conn, conn.transaction():
        return await record_funding_fee(
            conn,
            RecordFundingCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                funding_id=funding_id,
                amount=Money(amount=amount, currency=Currency.KRW),
                rate=Decimal("0.0001"),
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=ports.audit,
            clock=_clock,
        )


async def test_unknown_position_rejected(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnknownPositionError):
        await rebuild_snapshot(
            _key(tenant_id),
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
        )


async def test_healthy_snapshot_has_no_drift(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=Decimal("120"),
        fill_seq=2,
        order_id=order_id,
    )
    await _funding(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-2"),
        funding_id=str(uuid4()),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=True,
    )

    assert report.drift == {}
    assert report.applied is False
    assert report.entries == 3


async def test_dry_run_reports_drift_without_writing(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )

    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("999"),
        realized_pnl_base=Decimal("42"),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=True,
    )

    assert report.applied is False
    assert report.drift["quantity"] == (Decimal("999"), Decimal("10"))
    assert report.drift["realized_pnl_base"] == (Decimal("42"), Decimal("0"))

    async with pool.acquire() as conn:
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, realized_pnl_base FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert snapshot_row["quantity"] == Decimal("999")
    assert snapshot_row["realized_pnl_base"] == Decimal("42")


async def test_apply_fixes_drift_without_touching_journal(
    pool: asyncpg.Pool, ports: _RealPorts
) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=Decimal("120"),
        fill_seq=2,
        order_id=order_id,
    )

    async with pool.acquire() as conn:
        journal_count_before = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("0"),
        realized_pnl_base=Decimal("0"),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is True
    assert report.drift["quantity"] == (Decimal("0"), Decimal("6"))
    assert report.drift["realized_pnl_base"] == (
        Decimal("0"),
        (Decimal("120") - Decimal("100")) * Decimal("4"),
    )

    async with pool.acquire() as conn:
        journal_count_after = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, realized_pnl_base, last_journal_seq FROM pos_snapshot "
            "WHERE position_key = $1",
            position_key,
        )
    assert journal_count_after == journal_count_before == 2
    assert snapshot_row["quantity"] == Decimal("6")
    assert snapshot_row["realized_pnl_base"] == (Decimal("120") - Decimal("100")) * Decimal("4")
    assert snapshot_row["last_journal_seq"] == 2


async def test_apply_with_no_drift_is_noop(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=uuid4(),
    )

    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            "SELECT updated_at FROM pos_snapshot WHERE position_key = $1", position_key
        )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is False
    assert report.drift == {}

    async with pool.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT updated_at FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert before["updated_at"] == after["updated_at"]


class _QueryCountingConnectionCtx:
    """`pool.acquire()`의 async 컨텍스트 프록시 -- 내부 connection에 query
    logger를 달아 rebuild_snapshot()이 자체적으로 여는 왕복 수를 센다
    (`test_perf_journal_append.py`/`test_executor.py` ce2ce8ce와 동일 기법,
    단 rebuild_snapshot은 record_fill_in_position_ledger처럼 connection을
    노출하지 않고 자체 pool.acquire()를 여는 운영 도구라 pool 자체를 얇게
    감싼다)."""

    def __init__(self, inner_ctx: Any, sink: list[str]) -> None:
        self._inner_ctx = inner_ctx
        self._sink = sink
        self._conn: Any = None
        self._log: Any = None

    async def __aenter__(self) -> Any:
        self._conn = await self._inner_ctx.__aenter__()
        self._log = lambda record: self._sink.append(getattr(record, "query", ""))
        self._conn.add_query_logger(self._log)
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        if self._conn is not None and self._log is not None:
            self._conn.remove_query_logger(self._log)
        return await self._inner_ctx.__aexit__(exc_type, exc, tb)


class _QueryCountingPool:
    """rebuild_snapshot(pool, ...)가 유일하게 쓰는 `pool.acquire()`만 감싼다."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.queries: list[str] = []

    def acquire(self) -> _QueryCountingConnectionCtx:
        return _QueryCountingConnectionCtx(self._pool.acquire(), self.queries)


_MAX_REBUILD_ROUND_TRIPS = 8
_MAX_REBUILD_LATENCY_MS = 2000.0


@pytest.mark.perf
async def test_rebuild_snapshot_round_trip_and_latency_guard(
    pool: asyncpg.Pool, ports: _RealPorts
) -> None:
    """수치 성능 단언(DEEPEN task-2958) — DEPTH 감사(task-2723,
    docs/audit/DEPTH_LA_LB_LC.md #452)가 원 리프(2c9bf78)에 이 축 증빙이
    전무하다고 판정했다. task-2959/2962/2970/2974/2977과 같은 결정을
    따른다: 공유 CI 환경의 절대 지연은 이 파일이 통제할 수 없는 변동성을
    낳으므로, 구조 회귀 가드로 rebuild_snapshot() 1회(drift 존재 + 실제
    반영)의 순차 DB 왕복 수 상한(lock + get + list_for + upsert(쿼리 4건)
    + 쿼리 로거가 트랜잭션 경계(BEGIN/COMMIT)도 왕복으로 잡는다는 점까지
    합쳐 실측 6회, 여유 2 -> 8)을 걸고, 지연은 "무한정 걸리지 않는다"는
    느슨한 sanity 상한만 건다.
    """
    import time

    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("0"),
    )

    counting_pool = _QueryCountingPool(pool)
    started = time.perf_counter()
    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=counting_pool,
        clock=_clock,
        dry_run=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    round_trip_count = len(counting_pool.queries)

    print(
        f"\nrebuild_snapshot latency={elapsed_ms:.3f}ms "
        f"(sanity max={_MAX_REBUILD_LATENCY_MS}ms); "
        f"sequential DB round trips={round_trip_count} (max={_MAX_REBUILD_ROUND_TRIPS})"
    )
    assert report.applied is True
    assert round_trip_count <= _MAX_REBUILD_ROUND_TRIPS, (
        f"rebuild_snapshot 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_REBUILD_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
    assert elapsed_ms < _MAX_REBUILD_LATENCY_MS, (
        f"rebuild_snapshot 지연({elapsed_ms:.1f}ms)이 sanity 상한"
        f"({_MAX_REBUILD_LATENCY_MS}ms)을 초과했습니다."
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


async def test_funding_fee_rebuild_with_fee_applied(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    """음성 테스트 — 펀딩피가 기록된 포지션에 rebuild_snapshot(dry_run=False)
    을 호출하면 funding_base drift 가 정확히 보고되고 적용된다.
    fundings가 fold 에 제대로 반영되는지 검증."""
    tenant_id, account_id, position_key = await _open(pool)
    # Buy 10 @ 100
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=uuid4(),
    )

    # Record funding fee: positive amount
    funding_id = str(uuid4())
    await _funding(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("5"),
        funding_id=funding_id,
    )

    # Now corrupt snapshot's funding_base to create drift
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        funding_base=Decimal("0"),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is True
    assert "funding_base" in report.drift, (
        "펀팅피가 기록된 포지션에서 funding_base drift 가 없으면 fold 가 펀팅피를 누락한다"
    )
    assert report.drift["funding_base"] == (Decimal("0"), Decimal("5")), (
        "funding_base drift 값이 펀딩피 금액과 일치해야 한다"
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT funding_base, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert row["funding_base"] == Decimal("5"), (
        "rebuild_snapshot 적용 후 funding_base 가 펀딩피 금액으로 고쳐져야 한다"
    )
    assert row["last_journal_seq"] == 2, "펀딩피 1건 + 체결 1건 = last_journal_seq 2 여야 한다"


async def test_invalid_position_key_format_rejected(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    """음성 테스트 — 유효하지 않은 position_key format을 전달하면
    rebuild_snapshot이 PositionKey.parse() 단계에서 ValueError를 던진다.
    (1) separator 개수 부족, (2) UUID parsing 실패 등의 케이스를 확인."""
    tenant_id = await create_test_tenant(pool)

    invalid_keys = [
        "",  # empty string
        "TESTVENUE",  # too few parts
        "TESTVENUE:INST001:default",  # only 3 parts
        "TESTVENUE:INST001:default:paper",  # only 4 parts (missing portfolio_id)
        "TESTVENUE:INST001:default:paper:not-a-uuid",  # invalid UUID format
        "TESTVENUE:INST001:default:paper:",  # empty portfolio_id
    ]

    for invalid_key in invalid_keys:
        with pytest.raises(ValueError):
            await rebuild_snapshot(
                invalid_key,
                tenant_id=tenant_id,
                asset_class=AssetClass.CRYPTO,
                journal=ports.journal,
                snapshots=ports.snapshots,
                pool=pool,
                clock=_clock,
                dry_run=True,
            )
