"""FA-16 — adversarial: `record_fill` never changes `pos_snapshot` without a
matching `pos_journal` row, in the same transaction.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-16
(선행 FA-14=task-2050, FA-15=task-2060). FA-14가 정한 이벤트 원천을 그대로
쓴다 — 새 이벤트 테이블은 없다: 포지션의 이벤트 원천은 `pos_journal`
(`record_fill.py`, LB-11)이다. `journal.append(...)`가 돌려주는
`entry_view`는 스냅샷 fold(`snapshot_builder.apply_one`)의 *입력*이라,
저널 append 없이 스냅샷이 바뀌는 경로는 코드 구조상 존재하지 않는다.

DoD(1) "배선증명 없이 통과하는 테스트는 반려" —
`test_positions_record_fill_each_call_produces_exactly_one_journal_entry`는
`pos_journal` 행 수를 `pos_snapshot.last_journal_seq`와 직접 비교하므로,
`record_fill.py`의 `entry_view = await journal.append(...)` 줄을 지우면
다음 줄(`entry_view.sequence_no`)이 `AttributeError`로 즉시 죽어 이
테스트가 FAIL한다 — 실제로 그 줄을 임시로 주석 처리하고 이 테스트가
FAIL하는 것을 확인한 뒤 원복했다(회귀 방지를 위해 sabotage 코드는
커밋에 남기지 않는다).

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md #task-2061) 보강분(이 파일
하단): 위 확인은 커밋 메시지 서술로만 남고 리포에 자동 재현 가능한 형태로
보존되지 않았다 — 그리고 이 파일에는 성능 단언이 전혀 없었다.
`test_positions_bypassed_journal_write_is_caught_by_dod1_invariant_check`가
소스를 건드리지 않고 "append가 실제로 쓰지 않으면서 그럴듯한 값만
돌려주면?" 우회를 이중체로 영구 재현하고, `test_positions_record_fill_
round_trip_count_stays_bounded`가 `record_fill()` 왕복 수 회귀 가드를
더한다(절대 ms 대신 왕복 수 — task-920/1029 전례와 동일 이유).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

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
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import PositionJournalEntryView, RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_MAX_RECORD_FILL_ROUND_TRIPS = 12


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _position_key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="FA16ADV",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
        )
    )


def _fill_command(
    *, tenant_id, account_id, position_key, order_id, fill_seq: int
) -> RecordFillCommand:
    return RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        order_id=order_id,
        fill_seq=fill_seq,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Money(amount=Decimal("100"), currency=Currency.KRW),
        fee=None,
        occurred_at=_clock(),
        trace_id=uuid4(),
    )


class _BoomPositionJournal:
    async def append(self, conn, **kwargs):  # noqa: ANN001, ARG002 -- 테스트 전용
        raise RuntimeError("injected pos_journal append failure")


async def _open_position(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _position_key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def test_positions_record_fill_each_call_produces_exactly_one_journal_entry(pool):
    """Positive — DoD(1). 세 번의 fill 각각이 정확히 하나의 `pos_journal`
    행을 남기고, `pos_snapshot.last_journal_seq`가 그 개수와 일치한다."""
    tenant_id, account_id, position_key = await _open_position(pool)
    order_id = uuid4()
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    for fill_seq in (1, 2, 3):
        async with pool.acquire() as conn, conn.transaction():
            await record_fill(
                conn,
                _fill_command(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    position_key=position_key,
                    order_id=order_id,
                    fill_seq=fill_seq,
                ),
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_seq = await conn.fetchval(
            "SELECT last_journal_seq FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert journal_count == 3
    assert snapshot_seq == 3


async def test_positions_journal_append_failure_blocks_snapshot_change(pool):
    """Negative — DoD(3) fail-closed. `journal.append`(이벤트=`pos_journal`
    append)가 실패하면 스냅샷 fold까지 도달하지 못한다(`entry_view`가 fold의
    입력이라 순서상 불가능 — 구조적 fail-closed)."""
    tenant_id, account_id, position_key = await _open_position(pool)
    audit = PostgresAuditEventRepository(pool)

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="injected pos_journal append failure"):
            await record_fill(
                conn,
                _fill_command(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    position_key=position_key,
                    order_id=uuid4(),
                    fill_seq=1,
                ),
                asset_class=AssetClass.CRYPTO,
                journal=_BoomPositionJournal(),
                snapshots=PostgresSnapshotRepository(pool),
                audit=audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 0
    assert snapshot_row["quantity"] == Decimal("0")
    assert snapshot_row["last_journal_seq"] == 0


async def test_positions_transaction_rollback_removes_journal_and_snapshot_together(pool):
    """DoD(2) — 실DB. `record_fill`이 성공적으로 저널·스냅샷을 쓴 뒤에도
    (아직 커밋 전) 호출자의 트랜잭션이 롤백되면 둘 다 함께 사라진다."""
    tenant_id, account_id, position_key = await _open_position(pool)
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await record_fill(
            conn,
            _fill_command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=uuid4(),
                fill_seq=1,
            ),
            asset_class=AssetClass.CRYPTO,
            journal=journal,
            snapshots=snapshots,
            audit=audit,
            clock=_clock,
        )
        # 호출자의 더 큰 트랜잭션이 나중에 실패했다고 가정 — 명시적 롤백.
        await tx.rollback()

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 0
    assert snapshot_row["quantity"] == Decimal("0")
    assert snapshot_row["last_journal_seq"] == 0


class _LyingPositionJournal:
    """`append()`가 실제 `pos_journal` 행을 쓰지 않으면서 정상처럼 보이는
    `entry_view`만 돌려준다 — task-2061 원 커밋이 소스의 `entry_view =
    await journal.append(...)` 줄을 손으로 지웠다가 원복하며 수기로만
    확인한 우회("append가 사라져도 뒤 코드가 계속 진행되면?")를 소스를
    건드리지 않고 자동·영구 재현하는 이중체."""

    async def append(self, conn, **kwargs):  # noqa: ANN001, ARG002 -- 테스트 전용
        return PositionJournalEntryView(
            id=0,
            position_key=kwargs["position_key"],
            sequence_no=1,
            entry_type=kwargs["entry_type"],
            qty_delta=kwargs["qty_delta"],
            price=kwargs["price"],
            fee=kwargs["fee"],
            realized_pnl_base=kwargs["realized_pnl_base"],
            fx_rate=kwargs["fx_rate"],
            fx_source=kwargs["fx_source"],
            source_event_type=kwargs["source_event_type"],
            source_event_id=kwargs["source_event_id"],
            idempotency_key=kwargs["idempotency_key"],
            prev_hash=None,
            entry_hash="f" * 64,
            occurred_at=kwargs["occurred_at"],
            recorded_at=_clock(),
        )


async def test_positions_bypassed_journal_write_is_caught_by_dod1_invariant_check(pool):
    """우회재현(자동) — DEPTH task-2724 보강. task-2061 원 커밋은 이 우회를
    소스의 `entry_view = await journal.append(...)` 줄을 손으로 지웠다가
    복원하는 방식으로 한 번 확인하고 커밋 메시지에만 서술했다(리포에
    재현 가능한 형태로 남지 않음). 여기서는 `_LyingPositionJournal`로 그
    상황("append가 실제 쓰기 없이 그럴듯한 값만 돌려주면?")을 소스 변경
    없이 항상 재현한다 — `record_fill`은 `entry_view`가 존재하기만 하면
    계속 진행해 `pos_snapshot`을 갱신하므로(entry_view가 fold의 입력),
    `pos_journal`에는 행이 하나도 없는데 스냅샷은 바뀐다(I-10이 금지하는
    "이벤트 없는 상태 변경"). DoD(1) 양성 테스트가 쓰는 것과 같은 대조
    (저널 행수 vs `snapshot.last_journal_seq`)로 그 불일치를 직접
    확인한다."""
    tenant_id, account_id, position_key = await _open_position(pool)
    audit = PostgresAuditEventRepository(pool)

    async with pool.acquire() as conn, conn.transaction():
        await record_fill(
            conn,
            _fill_command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=uuid4(),
                fill_seq=1,
            ),
            asset_class=AssetClass.CRYPTO,
            journal=_LyingPositionJournal(),
            snapshots=PostgresSnapshotRepository(pool),
            audit=audit,
            clock=_clock,
        )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_seq = await conn.fetchval(
            "SELECT last_journal_seq FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert snapshot_seq == 1, "스냅샷은 이중체가 돌려준 entry_view로 갱신됐어야 한다"
    assert journal_count == 0, "이중체는 실제 pos_journal 행을 쓰지 않는다"
    assert journal_count != snapshot_seq, (
        f"FA-16/I-10 위반 재현: pos_journal 행 수({journal_count})와 pos_snapshot."
        f"last_journal_seq({snapshot_seq})가 불일치한다 — DoD(1) 양성 테스트의 대조가 "
        "실제로 이 우회를 잡아낸다는 자동·영구 증거."
    )


async def test_positions_record_fill_round_trip_count_stays_bounded(pool):
    """성능단언 — DEPTH task-2724 보강(이 파일에 성능 단언이 전혀 없었다).
    `record_fill()`(§7 B "측정 지점 record_fill") 1회의 순차 DB 왕복 수
    회귀 가드. 절대 ms 대신 왕복 수를 재는 이유는 이 저장소의 CI 절대지연
    게이트 금지 전례(task-920/1029, `test_perf_journal.py`)와 동일하다."""
    tenant_id, account_id, position_key = await _open_position(pool)
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    async with pool.acquire() as conn:
        await record_fill(
            conn,
            _fill_command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=uuid4(),
                fill_seq=1,
            ),
            asset_class=AssetClass.CRYPTO,
            journal=journal,
            snapshots=snapshots,
            audit=audit,
            clock=_clock,
        )

        queries: list[str] = []

        def _log(record: object) -> None:
            queries.append(getattr(record, "query", ""))

        conn.add_query_logger(_log)
        try:
            await record_fill(
                conn,
                _fill_command(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    position_key=position_key,
                    order_id=uuid4(),
                    fill_seq=2,
                ),
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )
        finally:
            conn.remove_query_logger(_log)

    print(f"\nrecord_fill() round trips: {len(queries)} (max={_MAX_RECORD_FILL_ROUND_TRIPS})")
    assert len(queries) <= _MAX_RECORD_FILL_ROUND_TRIPS, (
        f"record_fill() 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_RECORD_FILL_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
