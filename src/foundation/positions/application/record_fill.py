"""LB-11 — 체결(Fill) → 포지션 저널 기록의 단일 경로(application/record_fill).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9.3 LB-11.

순서는 LC-9 `post_entry.py`와 동일한 정신을 따른다: 락 → 멱등 사전 확인 →
규칙 계산 → 쓰기 → 감사. 다만 `PositionJournalRepository.append`(LB-9)이
`position_key` 단위 advisory lock과 idempotency 판정을 이미 자신의 트랜잭션
안에서 하므로(포트 docstring), 이 함수가 먼저 **같은 이름공간·같은 키**로
lock을 선점해 두는 이유는 원가법(FIFO/WEIGHTED) 계산이 "잠금 이후" 상태를
읽고 계산해야 하기 때문이다 — lock 없이 스냅샷을 먼저 읽으면(그 사이
동시 체결이 커밋될 수 있음) 오래된 lots 위에서 실현손익을 계산하는
경쟁상태가 생긴다. `journal.append`가 다시 같은 lock을 요청하는 것은
같은 트랜잭션 안에서는 즉시 반환되므로(재진입) 안전하다.

멱등 재입력은 `pos_journal.idempotency_key`에 이미 같은 키가 있는지(EXISTS,
UNIQUE 인덱스라 O(1)) 먼저 살펴 원가법 계산 자체를 건너뛴다 — 이미 소진된 로트 위에 같은 체결을
또 적용하면 (진짜로는 멱등인데도) `NegativeQuantityError`가 잘못 튀어나올
수 있기 때문이다(재전송은 성공해야 한다). 그래도 최종 판단은 `journal.append`
가 돌려주는 `sequence_no`로 한다 — `sequence_no <= snapshot.last_journal_seq`
면 이미 접힌 엔트리(REPLAY), 그렇지 않으면(`== last_journal_seq + 1`) 새
엔트리다. REPLAY는 스냅샷 upsert도, 감사 이벤트도 만들지 않는다(post_entry의
"digest 일치 REPLAY는 감사도 안 남긴다" 원칙과 동일 — 감사이벤트는 항상
저널 엔트리 하나당 정확히 하나, "1:1"). `idempotency_key`가 같은데 내용이
다르면(`POS_IDEMPOTENCY_DIGEST_MISMATCH`) `journal.append`가 예외를 던지고,
이 함수는 그대로 전파한다 — 호출자 버그이므로 재시도 불가, 감사는 남기지
않는다(taxonomy상 "불가" 등급이라 §4.3에 DENIED 감사 요구가 없다 — C 도메인
분개의 "감사 이벤트 없는 분개 없음"과 달리 B 도메인은 그 요구가 없다).

원가법·분개 규칙 자체는 재구현하지 않는다: 로트 시딩은
`snapshot_builder._seeded_cost_basis`와 같은 모양을 이 파일에도 두되(모듈
경계상 private 헬퍼를 다른 모듈이 import하지 않는다), 실제 원가 계산은
`cost_basis.selector.cost_basis_for` + `FifoLots/WeightedAverage.apply`
(LB-2/LB-3)이 하고, 저널 엔트리 입력 조립은 `journal_rules.fill_entry`
(LB-5)가 한다. 새 엔트리가 persist된 뒤의 스냅샷 접힘은
`snapshot_builder.apply_one`(LB-5)을 그대로 재사용한다 — 원가법을 두 번
계산하는 것처럼 보이지만(먼저 realized_pnl_base를 얻으려고 한 번,
`apply_one`이 저널 엔트리를 접으며 한 번) 둘 다 같은 로트·같은 입력에서
결정론적으로 같은 결과를 내므로 드리프트가 아니다 — `apply_one`이 실제
스냅샷의 유일한 "진실 계산" 경로라는 §4.3 "스냅샷 = fold(저널)" 불변을
어기지 않기 위한 의도적 선택이다.

`asset_class`는 `RecordFillCommand`(계약)에 없다 — `pos_account`/
`pos_snapshot`에도 아직 저장할 곳이 없다(instrument_ref 테이블 미착수,
LB-8 마이그레이션 주석 참고). 그래서 이 함수는 호출자가 이미 알고 있는
값(주문의 `asset_class`)을 키워드 인자로 받는다 — LB-12가 기존
`order.asset_class`를 그대로 넘기게 된다.

가격·수수료가 계좌 기준통화와 다른 통화면 `fx_rate`(호출자가 미리 조회한
`FXRate`)가 있어야 한다 — 0 대체 금지(`fx.FxRateMissingError`, 재시도
가능). 같은 통화면 `fx_rate`를 넘기지 않아도 된다.
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
    Lot,
    PositionSnapshotView,
    RecordFillCommand,
)
from src.foundation.positions.domain import fx, journal_rules
from src.foundation.positions.domain.cost_basis.fifo import FifoLots, FillEvent
from src.foundation.positions.domain.cost_basis.selector import cost_basis_for
from src.foundation.positions.domain.cost_basis.weighted import WeightedAverage
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, apply_one
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`POS_ACCOUNT_UNKNOWN` — `position_key`에 대응하는 `pos_snapshot` 행이
    없다. 저널 append 전에 (LB-11 밖의 어떤 경로가) 포지션을 열어 둬야
    한다는 LB-9 전제를 이 함수도 그대로 따른다 — 재시도 불가.

    `command.tenant_id`/`account_id`가 실제 소유자와 다를 때도 이 예외를
    그대로 재사용한다(task-489/LB-18 cross_tenant 적대적 테스트가 드러낸
    실결함의 수정 — 신규 에러코드를 만들지 않는다). "존재하지만 남의 것"과
    "아예 없음"을 호출자가 구분할 수 있게 하면 남의 position_key 존재
    여부를 흘리는 오라클이 되므로, 두 경우를 의도적으로 같은 예외 하나로
    합친다."""

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


def _seed_basis(
    template: FifoLots | WeightedAverage, lots: tuple[Lot, ...]
) -> FifoLots | WeightedAverage:
    """빈 큐/단일 로트로 원가법 엔진을 시딩한다(`snapshot_builder.
    _seeded_cost_basis`와 같은 모양 — private 헬퍼를 모듈 경계 밖으로 빌려
    쓰지 않기 위한 의도적 재선언, 원가 계산 로직 자체는 여기 없다)."""
    if isinstance(template, FifoLots):
        return FifoLots(lots)
    assert isinstance(template, WeightedAverage)
    return WeightedAverage(lots[0] if lots else None)


async def _acquire_position_lock(conn: asyncpg.Connection, position_key: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
        _LOCK_NAMESPACE,
        position_key,
    )


def _fx_multiplier(
    price: Money, base_currency: Currency, rate: FXRate | None
) -> tuple[Decimal | None, str | None]:
    """가격 통화 → 기준통화 배율 하나를 뽑는다. 같은 통화면 `(None, None)`
    (저널 행에 fx_rate 없음 = 이미 기준통화라는 뜻, `journal_rules`/
    `snapshot_builder`의 관례). `fx.convert(amount=1, ...)`로 배율만
    떼어내 재사용한다 — 방향(정/역환산) 판단은 `fx.convert`에 위임하고
    이 함수는 재구현하지 않는다."""
    if price.currency == base_currency:
        return None, None
    converted = fx.convert(Money(amount=Decimal("1"), currency=price.currency), base_currency, rate)
    assert converted.rate is not None
    return converted.rate.rate, converted.rate.source


async def record_fill(
    conn: asyncpg.Connection,
    command: RecordFillCommand,
    *,
    asset_class: AssetClass,
    journal: PositionJournalRepository,
    snapshots: SnapshotRepository,
    audit: AuditAppender,
    clock: Clock,
    fx_rate: FXRate | None = None,
) -> PositionSnapshotView:
    await _acquire_position_lock(conn, command.position_key)

    snapshot = await snapshots.get(conn, command.tenant_id, command.position_key)
    if snapshot is None or snapshot.account_id != command.account_id:
        raise UnknownPositionError(command.position_key)

    idempotency_key = f"fill:{command.order_id}:{command.fill_seq}"
    # `journal.list_for`(position_key의 전체 저널을 O(n) 조회)를 여기서만
    # 쓰려고 부르면 원가법 재계산 스킵 여부만 판단하는 데 과하다 —
    # `idempotency_key`는 `pos_journal`에 UNIQUE 인덱스가 있으므로 EXISTS로
    # O(1) 판정한다(§8.4 왕복 축소, task-653). 최종 신규/REPLAY 판정은 여전히
    # `journal.append`가 돌려주는 `sequence_no`로 한다(아래) — 이 EXISTS는
    # "원가법을 다시 계산해도 되는가"만 결정한다.
    is_replay_candidate = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pos_journal WHERE idempotency_key = $1)",
        idempotency_key,
    )

    if is_replay_candidate:
        realized_pnl_base = Decimal("0")
        fx_rate_value: Decimal | None = None
        fx_source_value: str | None = None
    else:
        template = cost_basis_for(snapshot.cost_method, asset_class)
        basis = _seed_basis(template, tuple(snapshot.lots))
        fill = FillEvent(
            side=command.side,
            quantity=command.quantity,
            price=command.price.amount,
            occurred_at=command.occurred_at,
        )
        result = basis.apply(fill)
        multiplier, fx_source_value = _fx_multiplier(
            command.price, snapshot.base_currency, fx_rate
        )
        raw_realized = result.realized_pnl * command.contract_multiplier
        realized_pnl_base = raw_realized * (multiplier if multiplier is not None else Decimal("1"))
        fx_rate_value = multiplier

    entry_input = journal_rules.fill_entry(
        order_id=command.order_id,
        fill_seq=command.fill_seq,
        side=command.side,
        quantity=command.quantity,
        price=command.price,
        fee=command.fee,
        realized_pnl_base=realized_pnl_base,
        fx_rate=fx_rate_value,
        fx_source=fx_source_value,
        occurred_at=command.occurred_at,
    )

    entry_view = await journal.append(
        conn,
        position_key=command.position_key,
        entry_type=JournalEntryType.FILL,
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
        "order_id": str(command.order_id),
        "fill_seq": command.fill_seq,
        "entry_id": entry_view.id,
        "sequence_no": entry_view.sequence_no,
        "qty_delta": str(entry_view.qty_delta),
        "realized_pnl_base": str(entry_view.realized_pnl_base),
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=command.tenant_id,
        aggregate_type="pos_journal_entry",
        aggregate_id=command.order_id,
        aggregate_revision=command.fill_seq,
        action="position.fill_recorded",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=command.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )

    return persisted
