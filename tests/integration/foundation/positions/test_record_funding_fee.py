"""LB-13 `record_funding_fee` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9.3 LB-13.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_funding_fee import (
    UnknownPositionError,
    record_funding_fee,
)
from src.foundation.positions.contracts.v1 import RecordFundingCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_FUNDING_ROUND_TRIPS = 12
_MAX_FUNDING_LATENCY_MS = 2000.0


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
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.snapshots = PostgresSnapshotRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


def _command(
    *,
    tenant_id,
    account_id,
    position_key,
    amount: Decimal = Decimal("-5"),
    rate: Decimal = Decimal("0.0001"),
    funding_id: str | None = None,
    occurred_at: datetime = _OCCURRED_AT,
) -> RecordFundingCommand:
    return RecordFundingCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        funding_id=funding_id or str(uuid4()),
        amount=Money(amount=amount, currency=Currency.KRW),
        rate=rate,
        occurred_at=occurred_at,
        trace_id=uuid4(),
    )


async def _open(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _record(pool, ports, command, *, audit=None):
    async with pool.acquire() as conn, conn.transaction():
        return await record_funding_fee(
            conn,
            command,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=audit or ports.audit,
            clock=_clock,
        )


async def test_funding_accrues_into_funding_base(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-5"),
    )

    snapshot = await _record(pool, ports, command)

    assert snapshot.funding_base == Decimal("-5")
    assert snapshot.quantity == Decimal("0")
    assert snapshot.last_journal_seq == 1


async def test_multiple_settlements_accumulate(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            amount=Decimal("-5"),
        ),
    )

    snapshot = await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            amount=Decimal("3"),
        ),
    )

    assert snapshot.funding_base == Decimal("-2")
    assert snapshot.last_journal_seq == 2


async def test_unknown_position_rejected(pool, ports):
    command = _command(
        tenant_id=uuid4(),
        account_id=uuid4(),
        position_key=_key(uuid4()),
        amount=Decimal("-1"),
    )

    with pytest.raises(UnknownPositionError):
        await _record(pool, ports, command)


async def test_replay_same_funding_id_returns_existing_without_duplicate(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-5"),
    )

    first = await _record(pool, ports, command)
    second = await _record(pool, ports, command)

    assert second.funding_base == first.funding_base
    assert second.last_journal_seq == first.last_journal_seq

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1", account_id
        )
    assert journal_count == 1
    assert audit_count == 1


async def test_audit_failure_rolls_back_journal_and_snapshot(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-5"),
    )

    with pytest.raises(RuntimeError):
        await _record(pool, ports, command, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT funding_base, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 0
    assert snapshot_row["funding_base"] == Decimal("0")
    assert snapshot_row["last_journal_seq"] == 0


@pytest.mark.perf
async def test_funding_settlement_round_trip_and_latency_guard(pool, ports):
    """수치 성능 단언(DEEPEN task-2958) — DEPTH 감사(task-2723,
    docs/audit/DEPTH_LA_LB_LC.md #452)가 원 리프(2c9bf78)에 이 축 증빙이
    전무하다고 판정했다. task-2959/2962/2970/2974/2977과 같은 결정을
    따른다: 공유 CI 환경의 절대 지연은 이 파일이 통제할 수 없는 변동성을
    낳으므로, 구조 회귀 가드로 record_funding_fee() 1회의 순차 DB 왕복
    수 상한(lock + get + journal.append(3왕복) + upsert + audit(2왕복) 구성,
    실측 9회, 여유 3 -> 12)을 걸고, 지연은 "무한정 걸리지 않는다"는 느슨한
    sanity 상한만 건다.
    """
    import time

    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-5"),
    )

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    started = time.perf_counter()
    async with pool.acquire() as conn, conn.transaction():
        conn.add_query_logger(_log)
        try:
            await record_funding_fee(
                conn,
                command,
                asset_class=AssetClass.CRYPTO,
                journal=ports.journal,
                snapshots=ports.snapshots,
                audit=ports.audit,
                clock=_clock,
            )
        finally:
            conn.remove_query_logger(_log)
    elapsed_ms = (time.perf_counter() - started) * 1000

    print(
        f"\nrecord_funding_fee latency={elapsed_ms:.3f}ms "
        f"(sanity max={_MAX_FUNDING_LATENCY_MS}ms); "
        f"sequential DB round trips={len(queries)} (max={_MAX_FUNDING_ROUND_TRIPS})"
    )
    assert len(queries) <= _MAX_FUNDING_ROUND_TRIPS, (
        f"record_funding_fee 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_FUNDING_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
    assert elapsed_ms < _MAX_FUNDING_LATENCY_MS, (
        f"record_funding_fee 지연({elapsed_ms:.1f}ms)이 sanity 상한"
        f"({_MAX_FUNDING_LATENCY_MS}ms)을 초과했습니다."
    )


async def test_bypassing_position_lock_causes_concurrent_funding_conflict_gate_red(
    pool, ports, monkeypatch
):
    """게이트 적색 재현(DEEPEN task-2958) + 동시성 증명 — 모듈독스트링이
    전제하는 `_acquire_position_lock`(pg_advisory_xact_lock)이 없다면 같은
    position_key에 대한 두 동시 정산이 서로의 last_journal_seq 변화를 보지
    못한 채 stale한 스냅샷 기준으로 fold를 시도한다 — 실측 결과 이 경우
    §4.3 연속성 규칙(`journal_rules.validate_sequence`, `new.seq ==
    prev.seq + 1`)이 105번 표준(조건부 UPDATE) upsert보다 먼저
    `SequenceConflictError`로 적색이 된다(fold_state가 stale snapshot
    기준 prev_seq=0인데 journal.append가 실제로 부여한 새 sequence_no는
    2이기 때문 — journal.append 자체의 advisory lock은 그대로라 append는
    성공하지만, 그 뒤 stale snapshot으로 계산한 fold가 연속성 위반을
    잡아낸다). 오늘(락이 있는 한) 이 경합이 나지 않는 이유가 바로 이
    락이라는 것의 반증(I-10 "우회불가"의 증거)이다. `PostgresSnapshotRepository.get`이
    반환하기 직전, 별도 커넥션에서 진짜 두 번째 `record_funding_fee`를
    완전히 커밋시켜 경합을 실제 SQL로 주입한다(mark_positions DEEPEN
    task-2977의 동일 기법) — 락이 살아 있었다면 이 두 번째 호출은 첫
    호출의 advisory lock이 풀릴 때까지 대기했을 것이다. 진 쪽의 저널
    엔트리는 (트랜잭션이 통째로 롤백돼) 아예 남지 않는다 — WORM 저널에
    반쪽짜리 행이 남지 않는다는 것도 함께 확인한다.
    """
    import src.foundation.positions.application.record_funding_fee as record_funding_fee_module
    from src.foundation.positions.domain.journal_rules import SequenceConflictError

    tenant_id, account_id, position_key = await _open(pool)

    async def _noop_lock(conn, key):
        return None

    monkeypatch.setattr(record_funding_fee_module, "_acquire_position_lock", _noop_lock)

    real_get = PostgresSnapshotRepository.get
    raced = False

    async def _get_then_race(self, conn, tenant_id_, position_key_):
        nonlocal raced
        snapshot = await real_get(self, conn, tenant_id_, position_key_)
        if not raced:
            raced = True
            race_command = _command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                amount=Decimal("-1"),
            )
            async with pool.acquire() as race_conn, race_conn.transaction():
                await record_funding_fee(
                    race_conn,
                    race_command,
                    asset_class=AssetClass.CRYPTO,
                    journal=ports.journal,
                    snapshots=ports.snapshots,
                    audit=ports.audit,
                    clock=_clock,
                )
        return snapshot

    monkeypatch.setattr(PostgresSnapshotRepository, "get", _get_then_race)

    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-5"),
    )

    with pytest.raises(SequenceConflictError):
        await _record(pool, ports, command)

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT funding_base, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 1, (
        "경합에서 진 정산의 저널 append는 스냅샷 upsert 실패로 트랜잭션 전체가 "
        "롤백돼야 한다(WORM 저널에 반쪽짜리 행이 남으면 안 된다)"
    )
    assert snapshot_row["last_journal_seq"] == 1
    assert snapshot_row["funding_base"] == Decimal("-1"), (
        "경합에서 이긴 첫 정산(race_command)만 스냅샷에 반영돼야 한다"
    )
