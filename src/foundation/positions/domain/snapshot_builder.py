"""LB-5 — 저널 엔트리 열을 접어(fold) `pos_snapshot`을 만드는 규칙(snapshot_builder).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9 LB-5.

§4.3 "스냅샷 = fold(저널)"을 그대로 구현한다: [[apply_one]]이 엔트리 하나를
누적 상태(`SnapshotFold`)에 접고, [[fold]]는 `functools.reduce(apply_one, ...)`
그 자체다 — 별도 최적화 경로를 두지 않는다. 재빌드(`rebuild_snapshot`, LB-13)가
저널만으로 스냅샷을 다시 만들 수 있어야 하므로, 원가법(FIFO/WEIGHTED) 로트도
매 `FILL` 엔트리마다 [[cost_basis.selector.cost_basis_for]]로 다시 계산한다
(LB-3 위임, 중복 구현 금지) — 엔트리에 이미 저장된 `realized_pnl_base`는
신뢰해 그대로 누적하고, 원가법 재적용은 로트/수량 갱신에만 쓴다.

`fees_base` 적립 규칙은 [[journal_rules]]와 공유한다: `entry.fee`가 있으면
`entry.fee.amount * (entry.fx_rate or 1)`을 더한다 — entry_type과 무관하게
전부 적용한다(체결에 딸린 수수료도, 독립 `FEE` 엔트리도 같은 컬럼을 쓰므로).
`funding_base`는 `FUNDING` 엔트리의 `realized_pnl_base` 컬럼(재사용, §
[[journal_rules.funding_entry]] 참고)에서만 적립한다.

순수 도메인(DB/HTTP import 0).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import reduce

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    JournalEntryType,
    Lot,
    PositionJournalEntryView,
)
from src.foundation.positions.domain.cost_basis.fifo import FifoLots, FillEvent
from src.foundation.positions.domain.cost_basis.selector import CostBasis, cost_basis_for
from src.foundation.positions.domain.cost_basis.weighted import WeightedAverage
from src.foundation.positions.domain.journal_rules import validate_sequence


class UnsupportedEntryTypeError(ValueError):
    """`apply_one`이 아직 접는 법을 모르는 `entry_type`(`ADJUSTMENT`,
    `CORP_ACTION` — 이 리프 범위 밖). [[cost_basis.selector.
    UnknownAssetClassError]]와 같은 전례를 따라 침묵 스킵 대신 예외로
    드러낸다 — LB-1 taxonomy에 정확히 대응하는 `POS_*` 코드는 없다."""

    def __init__(self, position_key: str, entry_type: JournalEntryType) -> None:
        super().__init__(f"{position_key}: 지원하지 않는 entry_type입니다: {entry_type.value}")
        self.position_key = position_key
        self.entry_type = entry_type


@dataclass(frozen=True, slots=True)
class SnapshotFold:
    """`pos_snapshot`의 접힘 가능한 부분집합(§9 LB-5 DoD가 명시한 필드만 —
    `tenant_id`/`account_id`/`instrument_id`/`base_currency`/`updated_at` 등
    계좌 정적 컨텍스트는 저널만으로 알 수 없으므로 이 타입에 없다. 전체
    `PositionSnapshotView`는 호출자가 이 결과에 그 컨텍스트를 얹어 만든다)."""

    quantity: Decimal = Decimal("0")
    avg_cost: Decimal = Decimal("0")
    lots: tuple[Lot, ...] = ()
    realized_pnl_base: Decimal = Decimal("0")
    fees_base: Decimal = Decimal("0")
    funding_base: Decimal = Decimal("0")
    last_journal_seq: int = 0


def _seeded_cost_basis(
    cost_method: CostMethod, asset_class: AssetClass, lots: tuple[Lot, ...]
) -> CostBasis:
    """[[cost_basis.selector.cost_basis_for]]로 FIFO/WEIGHTED 중 무엇을 쓸지
    고르되(asset_class가 파생상품이면 WEIGHTED 강제 — 위임, 중복 구현 금지),
    selector가 늘 빈 인스턴스를 반환하므로 여기서 기존 `lots`를 시드한다."""
    template = cost_basis_for(cost_method, asset_class)
    if isinstance(template, FifoLots):
        return FifoLots(lots)
    assert isinstance(template, WeightedAverage)
    return WeightedAverage(lots[0] if lots else None)


def _avg_cost(quantity: Decimal, lots: tuple[Lot, ...]) -> Decimal:
    if quantity <= 0:
        return Decimal("0")
    total_cost = sum((lot.quantity * lot.unit_cost for lot in lots), Decimal("0"))
    return total_cost / quantity


def apply_one(
    state: SnapshotFold,
    entry: PositionJournalEntryView,
    *,
    position_key: str,
    cost_method: CostMethod,
    asset_class: AssetClass,
) -> SnapshotFold:
    """엔트리 하나를 `state`에 접는다. §4.3 "(position_key, sequence_no) 유일·
    연속" — 순서가 뒤바뀌거나 건너뛴 엔트리는 `SequenceConflictError`로
    거부한다(`journal_rules.validate_sequence` 재사용)."""
    validate_sequence(position_key, state.last_journal_seq, entry.sequence_no)

    quantity = state.quantity
    lots = state.lots
    realized_pnl_base = state.realized_pnl_base
    funding_base = state.funding_base

    if entry.entry_type is JournalEntryType.FILL:
        if entry.price is None:
            raise ValueError(
                f"{position_key}: FILL 엔트리는 price가 필요합니다(seq={entry.sequence_no})."
            )
        basis = _seeded_cost_basis(cost_method, asset_class, lots)
        fill = FillEvent(
            side=OrderSide.BUY if entry.qty_delta > 0 else OrderSide.SELL,
            quantity=abs(entry.qty_delta),
            price=entry.price.amount,
            occurred_at=entry.occurred_at,
        )
        result = basis.apply(fill)
        lots = result.lots
        quantity = sum((lot.quantity for lot in lots), Decimal("0"))
        realized_pnl_base = realized_pnl_base + entry.realized_pnl_base
    elif entry.entry_type is JournalEntryType.FUNDING:
        funding_base = funding_base + entry.realized_pnl_base
    elif entry.entry_type is JournalEntryType.FEE:
        pass
    else:
        raise UnsupportedEntryTypeError(position_key, entry.entry_type)

    fees_base = state.fees_base
    if entry.fee is not None:
        multiplier = entry.fx_rate if entry.fx_rate is not None else Decimal("1")
        fees_base = fees_base + entry.fee.amount * multiplier

    return SnapshotFold(
        quantity=quantity,
        avg_cost=_avg_cost(quantity, lots),
        lots=lots,
        realized_pnl_base=realized_pnl_base,
        fees_base=fees_base,
        funding_base=funding_base,
        last_journal_seq=entry.sequence_no,
    )


def fold(
    entries: Sequence[PositionJournalEntryView],
    *,
    position_key: str,
    cost_method: CostMethod,
    asset_class: AssetClass,
    initial: SnapshotFold | None = None,
) -> SnapshotFold:
    """`entries`(sequence_no 오름차순)를 처음부터 접는다 —
    `functools.reduce(apply_one, entries, initial)` 그 자체(결정성·결합성은
    `apply_one`이 순수 함수이므로 자동으로 따라온다)."""
    start = initial if initial is not None else SnapshotFold()
    return reduce(
        lambda acc, entry: apply_one(
            acc, entry, position_key=position_key, cost_method=cost_method, asset_class=asset_class
        ),
        entries,
        start,
    )
