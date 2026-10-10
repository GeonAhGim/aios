"""FA-14 통합테스트 — 실DB(TEST_DATABASE_URL)에서 `pos_journal` 전건을 재생한
`positions` 투영이 현재 `pos_snapshot`과 필드 단위로 같은지 검증한다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-14 DoD.
DoD(1) 필드 단위 동일(불일치 1건이면 FAIL). DoD(2) 배선증명 negative — 원천
이벤트 1건을 고의로 빼면 대조가 실제로 FAIL함을 같은 테스트에서 단언한다
(항상 통과하는 대조는 반려).

`orders` 투영 검증은 `test_projections.py`, `ledger` 투영 검증은
`test_projections_ledger.py`로 분리돼 있다(책임 단위 분할,
CLAUDE.md ADR-2026-09-10-C §7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.eventstore.projections import positions as positions_projection
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository as PositionsJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    JournalEntryType,
    PositionJournalEntryView,
    RecordFillCommand,
)
from src.foundation.positions.domain import journal_rules
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------- positions ---


async def _record_two_fills(pool) -> tuple:
    journal = PositionsJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    order_id = uuid4()

    async def _fill(side: OrderSide, quantity: Decimal, price: Decimal, fill_seq: int):
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
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )

    await _fill(OrderSide.BUY, Decimal("10"), Decimal("100"), 1)
    await _fill(OrderSide.SELL, Decimal("4"), Decimal("120"), 2)

    async with pool.acquire() as conn:
        entries = await journal.list_for(conn, position_key)
        row = await conn.fetchrow(
            "SELECT quantity, avg_cost, realized_pnl_base, fees_base, funding_base, "
            "last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    return position_key, entries, row


async def test_positions_projection_matches_current_snapshot_after_full_replay(pool):
    position_key, entries, row = await _record_two_fills(pool)

    folded = positions_projection.project(
        entries,
        position_key=position_key,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )

    assert folded.quantity == row["quantity"]
    assert folded.avg_cost == row["avg_cost"]
    assert folded.realized_pnl_base == row["realized_pnl_base"]
    assert folded.fees_base == row["fees_base"]
    assert folded.funding_base == row["funding_base"]
    assert folded.last_journal_seq == row["last_journal_seq"]


async def test_positions_projection_detects_dropped_entry(pool):
    """DoD(2) 배선증명: 원천 저널 엔트리 1건(두 번째 체결)을 빼면 재생
    수량이 현재 스냅샷과 실제로 달라진다."""
    position_key, entries, row = await _record_two_fills(pool)
    assert len(entries) == 2

    dropped = positions_projection.project(
        entries[:1],
        position_key=position_key,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )

    assert dropped.quantity != row["quantity"]


async def test_positions_projection_rejects_fill_entry_missing_price(pool):
    """실패 주입(D2): 이벤트가 빠진 게 아니라, 실제로 존재하는 FILL 엔트리의
    `price` 필드가 백엔드 회귀(예: 직렬화 버그로 원본 체결가 유실)로 손상된
    경우를 흉내낸다 — `apply_one`이 조용히 스킵하지 않고 ValueError로
    드러내는지 확인한다(늘 통과하는 대조가 아님)."""
    position_key, entries, _row = await _record_two_fills(pool)
    corrupted = entries[0].model_copy(update={"price": None})

    with pytest.raises(ValueError, match="price"):
        positions_projection.project(
            [corrupted],
            position_key=position_key,
            cost_method=CostMethod.FIFO,
            asset_class=AssetClass.CRYPTO,
        )


async def _record_three_fills(pool) -> tuple:
    """`_record_two_fills`와 같은 계좌 설정에 체결을 하나 더 쌓아, 시퀀스
    가운데(2번)를 건너뛴 재생이 `SequenceConflictError`로 실제 발동하는지
    볼 수 있는 3건짜리 저널을 만든다."""
    journal = PositionsJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    order_id = uuid4()

    async def _fill(side: OrderSide, quantity: Decimal, price: Decimal, fill_seq: int):
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
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )

    await _fill(OrderSide.BUY, Decimal("10"), Decimal("100"), 1)
    await _fill(OrderSide.SELL, Decimal("4"), Decimal("120"), 2)
    await _fill(OrderSide.SELL, Decimal("2"), Decimal("130"), 3)

    async with pool.acquire() as conn:
        entries = await journal.list_for(conn, position_key)
    return position_key, entries


async def test_positions_projection_rejects_sequence_gap_from_dropped_middle_entry(pool):
    """게이트 적색 재현(D2): §4.3 연속성 불변을 강제하는 `journal_rules.
    validate_sequence`(POS_SEQUENCE_CONFLICT)가 실제로 배선돼 있음을, 값
    분기가 아니라 그 가드가 직접 던지는 예외로 증명한다 — 가운데 엔트리
    (2번)를 빼고 1·3번만 재생하면 3번의 sequence_no=3이 기대값(prev+1=2)과
    달라 즉시 거부된다."""
    position_key, entries = await _record_three_fills(pool)
    assert [e.sequence_no for e in entries] == [1, 2, 3]

    with pytest.raises(journal_rules.SequenceConflictError):
        positions_projection.project(
            [entries[0], entries[2]],
            position_key=position_key,
            cost_method=CostMethod.FIFO,
            asset_class=AssetClass.CRYPTO,
        )


@pytest.mark.perf
def test_positions_projection_folds_ten_thousand_fee_entries_under_budget(
    perf_budget,
) -> None:
    """성능 단언(D2): `project()`는 순수 fold(모듈 docstring, I/O 없음)라 DB
    없이도 측정할 수 있다 — `apply_one`이 엔트리마다 로트 전체를 다시
    스캔하는 등 O(n) 밖의 비용을 갖고 있지 않은지 10,000건으로 상한을
    건다.

    raw perf_counter() → perf_budget.assert_within(batch=10) 전환:
    process_time의 15.6ms 틱으로 인해 빠른 1회 호출은 0/15.6만 나오므로,
    batch=10으로 묶어 호출당 오차를 tick/10 ≈ 1.6ms로 낮췄다.
    단위는 samples.cpu_ms가 ms이므로 예산도 ms(초预算은 *1000 한 번).
    """
    position_key = f"perf-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    entries = [
        PositionJournalEntryView(
            id=seq,
            position_key=position_key,
            sequence_no=seq,
            entry_type=JournalEntryType.FEE,
            qty_delta=Decimal("0"),
            price=None,
            fee=Money(amount=Decimal("0.01"), currency=Currency.KRW),
            realized_pnl_base=Decimal("0"),
            fx_rate=None,
            fx_source=None,
            source_event_type="fee",
            source_event_id=f"fee-{seq}",
            idempotency_key=f"fee:{seq}",
            prev_hash=None,
            entry_hash="e" * 64,
            occurred_at=now,
            recorded_at=now,
        )
        for seq in range(1, 10_001)
    ]

    folded = positions_projection.project(
        entries,
        position_key=position_key,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )
    assert folded.fees_base == Decimal("100.00")

    perf_budget.assert_within(
        lambda: positions_projection.project(
            entries,
            position_key=position_key,
            cost_method=CostMethod.FIFO,
            asset_class=AssetClass.CRYPTO,
        ),
        budget_ms=800,
        n=5,
        warmup=1,
        batch=10,
        label="10k fee entries fold",
    )
