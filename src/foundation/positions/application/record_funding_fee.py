"""LB-13 — 펀딩피 정산 → 포지션 저널 기록의 단일 경로(application/record_funding_fee).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9.3 LB-13.

`record_fill`(LB-11, [[record_fill]])과 같은 정신(락 → 조회 → 규칙 계산 →
쓰기 → 감사)을 따르되 훨씬 단순하다: 펀딩은 원가법 로트를 건드리지 않는다
(`qty_delta=0`, [[journal_rules.funding_entry]]) — 그래서 `record_fill`처럼
"멱등 재입력이면 계산을 건너뛴다"는 사전 분기가 필요 없다. 같은
`funding_id`로 재전송돼도 `amount_base`를 다시 계산하는 것 자체가
부작용이 없고(로트 소진 같은 상태 변화가 없다), 최종 판단은 여전히
`journal.append`가 돌려주는 `sequence_no`로 한다
(`sequence_no <= snapshot.last_journal_seq`면 이미 접힌 REPLAY).

`RecordFundingCommand.amount`는 호출자가 이미 계산해 온 정산액이다
([[domain.funding_fees.funding_amount]]는 이 값을 만드는 상류(거래소 펀딩
수집 경로, 아직 미착수)의 책임이지 이 리프의 책임이 아니다) — 이 함수는
그 금액을 기준통화로 환산([[domain.fx.convert]] 위임, `record_fill`의
`_fx_multiplier`와 같은 모양을 사서 재선언한다 — 모듈 경계상 private
헬퍼를 빌려 쓰지 않는다는 같은 이유)하고 저널에 적을 뿐이다.
`RecordFundingCommand.rate`는 계산에 쓰이지 않는다(이미 `amount`에 반영된
값) — 감사 payload에만 원인 추적용으로 남긴다.

저널 append 후 스냅샷 접힘은 `record_fill`과 동일하게
`snapshot_builder.apply_one`(LB-5, 유일한 "진실 계산" 경로)을 재사용한다.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.data.models.base import AssetClass, Currency, FXRate, Money
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.positions.contracts.v1 import (
    JournalEntryType,
    PositionSnapshotView,
    RecordFundingCommand,
)
from src.foundation.positions.domain import fx, journal_rules
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, apply_one
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`POS_ACCOUNT_UNKNOWN` — `position_key`에 대응하는 `pos_snapshot` 행이
    없다([[record_fill.UnknownPositionError]]와 같은 전제·재선언). 재시도
    불가."""

    def __init__(self, position_key: str) -> None:
        super().__init__(f"알 수 없는 position_key(스냅샷 없음): {position_key!r}")
        self.position_key = position_key


class AuditAppender(Protocol):
    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


async def _acquire_position_lock(conn: asyncpg.Connection, position_key: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
        _LOCK_NAMESPACE,
        position_key,
    )


def _fx_multiplier(
    amount: Money, base_currency: Currency, rate: FXRate | None
) -> tuple[Decimal | None, str | None]:
    """[[record_fill._fx_multiplier]]와 같은 모양(모듈 경계상 재선언) —
    같은 통화면 `(None, None)`."""
    if amount.currency == base_currency:
        return None, None
    converted = fx.convert(
        Money(amount=Decimal("1"), currency=amount.currency), base_currency, rate
    )
    assert converted.rate is not None
    return converted.rate.rate, converted.rate.source


async def record_funding_fee(
    conn: asyncpg.Connection,
    command: RecordFundingCommand,
    *,
    asset_class: AssetClass,
    journal: PositionJournalRepository,
    snapshots: SnapshotRepository,
    audit: AuditAppender,
    clock: Clock,
    fx_rate: FXRate | None = None,
) -> PositionSnapshotView:
    await _acquire_position_lock(conn, command.position_key)

    snapshot = await snapshots.get(conn, command.position_key)
    if snapshot is None:
        raise UnknownPositionError(command.position_key)

    multiplier, fx_source = _fx_multiplier(command.amount, snapshot.base_currency, fx_rate)
    amount_base = command.amount.amount * (multiplier if multiplier is not None else Decimal("1"))

    entry_input = journal_rules.funding_entry(
        funding_id=command.funding_id,
        amount_base=amount_base,
        fx_rate=multiplier,
        fx_source=fx_source,
        occurred_at=command.occurred_at,
    )

    entry_view = await journal.append(
        conn,
        position_key=command.position_key,
        entry_type=JournalEntryType.FUNDING,
        qty_delta=entry_input.qty_delta,
        price=entry_input.price,
        fee=entry_input.fee,
        realized_pnl_base=entry_input.realized_pnl_base,
        fx_rate=entry_input.fx_rate,
        fx_source=entry_input.fx_source,
        source_event_type=entry_input.source_event_type,
        source_event_id=entry_input.source_event_id,
        idempotency_key=entry_input.idempotency_key,
        occurred_at=entry_input.occurred_at,
    )

    if entry_view.sequence_no <= snapshot.last_journal_seq:
        return snapshot

    fold_state = SnapshotFold(
        quantity=snapshot.quantity,
        lots=tuple(snapshot.lots),
        realized_pnl_base=snapshot.realized_pnl_base,
        fees_base=snapshot.fees_base,
        funding_base=snapshot.funding_base,
        last_journal_seq=snapshot.last_journal_seq,
    )
    new_fold = apply_one(
        fold_state,
        entry_view,
        position_key=command.position_key,
        cost_method=snapshot.cost_method,
        asset_class=asset_class,
    )

    new_snapshot = snapshot.model_copy(
        update={
            "quantity": new_fold.quantity,
            "avg_cost": Money(amount=new_fold.avg_cost, currency=snapshot.avg_cost.currency),
            "lots": list(new_fold.lots),
            "realized_pnl_base": new_fold.realized_pnl_base,
            "fees_base": new_fold.fees_base,
            "funding_base": new_fold.funding_base,
            "last_journal_seq": new_fold.last_journal_seq,
            "updated_at": clock(),
        }
    )
    persisted = await snapshots.upsert(conn, new_snapshot, expected_seq=snapshot.last_journal_seq)

    payload: dict[str, object] = {
        "position_key": command.position_key,
        "funding_id": command.funding_id,
        "entry_id": entry_view.id,
        "sequence_no": entry_view.sequence_no,
        "amount": str(command.amount.amount),
        "amount_ccy": command.amount.currency.value,
        "rate": str(command.rate),
        "amount_base": str(amount_base),
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=command.tenant_id,
        aggregate_type="pos_journal_entry",
        aggregate_id=command.account_id,
        aggregate_revision=entry_view.sequence_no,
        action="position.funding_recorded",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=command.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )

    return persisted
