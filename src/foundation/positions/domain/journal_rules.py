"""LB-5 — 체결·펀딩피·수수료 사건 → `pos_journal` 엔트리 변환 규칙(journal_rules).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-5.

세 팩토리(`fill_entry`/`funding_entry`/`fee_entry`)는 [[ports/journal_repository.
PositionJournalRepository.append]]에 그대로 넘길 수 있는 `JournalEntryInput`을
만든다 — `sequence_no`/`id`/`prev_hash`/`entry_hash`/`recorded_at`는 advisory
lock 하에서 어댑터(LB-9)가 채우므로 이 리프의 산출물에는 없다. `digest`는
§5 저널 append 표의 공식("digest = sha256(qty_delta, price, fee, occurred_at)")을
그대로 구현한다 — `realized_pnl_base`는 의도적으로 제외한다(재전송 판정은
같은 사건이 같은 체결/수수료를 냈는지만 보면 충분하고, 파생 손익까지 다시
비교하면 부동소수 없는 `Decimal`이라도 계산 경로가 늘어날수록 재전송 오탐
표면적이 커진다).

`fee`(원래 통화)를 기준통화로 접는 규칙은 이 모듈과 [[snapshot_builder]]가
공유한다: `fx_rate`가 있으면 `fee.amount * fx_rate`, 없으면(이미 기준통화)
`fee.amount` 그대로 — LB-4 `fx.FXRate`(base/quote 쌍)를 저널 행에 통째로
저장하지 않고 배율 하나만 남기기 위한 이 리프의 설계 결정이다(LB-8 스키마에
`fee_ccy`는 있어도 `fee_base`는 없음). `funding_entry`/`fee_entry`가 받는
금액은 이미 이 배율로 기준통화 환산이 끝난 값이라고 가정한다 — 환율 조회
자체는 [[fx.convert]]/[[funding_fees.to_base]](LB-4)의 책임이고 이 모듈은
호출하지 않는다.

순수 도메인(DB/HTTP import 0) — 시각은 항상 인자로 받는다.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.data.models.base import Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import (
    JournalEntryType,
    PositionErrorCode,
    PositionJournalEntryView,
)


class SequenceConflictError(Exception):
    """`POS_SEQUENCE_CONFLICT` — §4.3 "(position_key, sequence_no) 유일·연속
    (1부터)" 위반. 재시도 가능(재조회 후 재시도)."""

    code = PositionErrorCode.SEQUENCE_CONFLICT

    def __init__(self, position_key: str, expected: int, actual: int) -> None:
        super().__init__(
            f"{position_key}: sequence_no는 {expected}이어야 하는데 {actual}이(가) 왔습니다."
        )
        self.position_key = position_key
        self.expected = expected
        self.actual = actual


def validate_sequence(position_key: str, prev_seq: int, new_seq: int) -> None:
    """§4.3 `new.seq == prev.seq + 1`. `prev_seq=0`이 저널 비어 있음(최초
    엔트리는 `sequence_no=1`)."""
    if new_seq != prev_seq + 1:
        raise SequenceConflictError(position_key, prev_seq + 1, new_seq)


def _money_token(m: Money | None) -> str:
    return f"{m.amount}:{m.currency.value}" if m is not None else ""


def digest_for(
    qty_delta: Decimal, price: Money | None, fee: Money | None, occurred_at: datetime
) -> str:
    """§5 저널 append 멱등성: `digest = sha256(qty_delta, price, fee,
    occurred_at)`. 같은 `idempotency_key` 재전송이 이 값과 다르면
    `POS_IDEMPOTENCY_DIGEST_MISMATCH`(어댑터 LB-9의 책임)."""
    payload = "|".join(
        [str(qty_delta), _money_token(price), _money_token(fee), occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def entry_hash_for(
    prev_hash: str | None,
    sequence_no: int,
    entry_type: JournalEntryType,
    digest: str,
    occurred_at: datetime,
) -> str:
    """체인의 링크 하나(LC-3 `hash_chain.entry_hash`와 같은 패턴). `prev_hash`가
    `None`이면(전역 첫 엔트리) 빈 문자열로 취급해 결정론적으로 시작한다."""
    payload = "|".join(
        [prev_hash or "", str(sequence_no), entry_type.value, digest, occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChainIntegrityError(Exception):
    """저널 해시 체인 단절 또는 변조 의심(`prev_hash` 불일치, 또는 필드로부터
    재계산한 `entry_hash`가 저장값과 다름). LB-1 taxonomy에는 이 정확한
    경우를 위한 `POS_*` 코드가 없다(가장 가까운 `POS_NAV_CHAIN_BROKEN`은 NAV
    전용) — [[cost_basis.selector.UnknownAssetClassError]]와 같은 전례를 따라
    코드 매핑 없이 던진다."""

    def __init__(self, position_key: str, sequence_no: int, reason: str) -> None:
        super().__init__(f"{position_key} seq={sequence_no}: {reason}")
        self.position_key = position_key
        self.sequence_no = sequence_no


def verify_chain(position_key: str, entries: Sequence[PositionJournalEntryView]) -> None:
    """`sequence_no` 오름차순으로 정렬된 저널 목록의 해시 체인을 검증한다.
    문제 없으면 조용히 반환하고, 있으면 `ChainIntegrityError`를 던진다."""
    expected_prev: str | None = None
    for entry in entries:
        if entry.prev_hash != expected_prev:
            raise ChainIntegrityError(
                position_key,
                entry.sequence_no,
                "prev_hash가 이전 엔트리의 entry_hash와 일치하지 않습니다(체인 단절 또는 변조).",
            )
        digest = digest_for(entry.qty_delta, entry.price, entry.fee, entry.occurred_at)
        recomputed = entry_hash_for(
            entry.prev_hash, entry.sequence_no, entry.entry_type, digest, entry.occurred_at
        )
        if recomputed != entry.entry_hash:
            raise ChainIntegrityError(
                position_key,
                entry.sequence_no,
                "entry_hash가 필드로부터 재계산한 값과 다릅니다(내용 변조 의심).",
            )
        expected_prev = entry.entry_hash


@dataclass(frozen=True, slots=True)
class JournalEntryInput:
    """`PositionJournalRepository.append`(LB-7)에 그대로 넘기는 입력. `digest`는
    어댑터가 재전송 판정에 쓴다(저장 컬럼이지만 LB-1 뷰에는 없음 — §5 참고)."""

    entry_type: JournalEntryType
    qty_delta: Decimal
    price: Money | None
    fee: Money | None
    realized_pnl_base: Decimal
    fx_rate: Decimal | None
    fx_source: str | None
    source_event_type: str
    source_event_id: str
    idempotency_key: str
    occurred_at: datetime
    digest: str


def fill_entry(
    *,
    order_id: UUID,
    fill_seq: int,
    side: OrderSide,
    quantity: Decimal,
    price: Money,
    fee: Money | None,
    realized_pnl_base: Decimal,
    fx_rate: Decimal | None,
    fx_source: str | None,
    occurred_at: datetime,
) -> JournalEntryInput:
    """체결 하나 → `FILL` 엔트리. 멱등키는 `f"fill:{order_id}:{fill_seq}"`
    (§3.2 `RecordFillCommand` 계약과 동일한 스킴). `realized_pnl_base`는
    이미 원가법(selector 경유 FIFO/WEIGHTED)·기준통화 환산이 끝난 값을
    호출자(LB-11 `record_fill`)가 넘긴다 — 이 함수는 원가 계산을 하지 않는다."""
    if quantity <= 0:
        raise ValueError(f"quantity는 양수여야 합니다: {quantity}")
    qty_delta = quantity if side is OrderSide.BUY else -quantity
    return JournalEntryInput(
        entry_type=JournalEntryType.FILL,
        qty_delta=qty_delta,
        price=price,
        fee=fee,
        realized_pnl_base=realized_pnl_base,
        fx_rate=fx_rate,
        fx_source=fx_source,
        source_event_type="fill",
        source_event_id=f"{order_id}:{fill_seq}",
        idempotency_key=f"fill:{order_id}:{fill_seq}",
        occurred_at=occurred_at,
        digest=digest_for(qty_delta, price, fee, occurred_at),
    )


def funding_entry(
    *,
    funding_id: str,
    amount_base: Decimal,
    fx_rate: Decimal | None = None,
    fx_source: str | None = None,
    occurred_at: datetime,
) -> JournalEntryInput:
    """펀딩피 정산 하나 → `FUNDING` 엔트리. 수량은 바뀌지 않는다(`qty_delta=0`).
    멱등키는 `f"funding:{funding_id}"`(§3.2 `RecordFundingCommand`와 동일).
    `amount_base`는 [[snapshot_builder.apply_one]]에서 `funding_base`로
    적립된다(§4.3이 `pos_journal`에 `funding_base` 전용 컬럼을 두지 않으므로
    `realized_pnl_base` 컬럼을 재사용 — entry_type으로 라우팅)."""
    qty_delta = Decimal("0")
    return JournalEntryInput(
        entry_type=JournalEntryType.FUNDING,
        qty_delta=qty_delta,
        price=None,
        fee=None,
        realized_pnl_base=amount_base,
        fx_rate=fx_rate,
        fx_source=fx_source,
        source_event_type="funding",
        source_event_id=funding_id,
        idempotency_key=f"funding:{funding_id}",
        occurred_at=occurred_at,
        digest=digest_for(qty_delta, None, None, occurred_at),
    )


def fee_entry(
    *,
    source_event_id: str,
    fee: Money,
    fx_rate: Decimal | None = None,
    fx_source: str | None = None,
    occurred_at: datetime,
) -> JournalEntryInput:
    """체결에 딸리지 않은 독립 수수료(예: 출금 수수료) → `FEE` 엔트리. 수량·
    실현손익 모두 바뀌지 않는다. 멱등키는 `f"fee:{source_event_id}"`."""
    qty_delta = Decimal("0")
    return JournalEntryInput(
        entry_type=JournalEntryType.FEE,
        qty_delta=qty_delta,
        price=None,
        fee=fee,
        realized_pnl_base=Decimal("0"),
        fx_rate=fx_rate,
        fx_source=fx_source,
        source_event_type="fee",
        source_event_id=source_event_id,
        idempotency_key=f"fee:{source_event_id}",
        occurred_at=occurred_at,
        digest=digest_for(qty_delta, None, fee, occurred_at),
    )
