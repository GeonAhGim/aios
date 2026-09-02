"""LB-2 — FIFO 원가법 로트 큐.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2
(`domain/cost_basis/fifo.py`: "FIFO 로트 큐: 매수는 로트 push, 매도는
head부터 소진, 실현손익 = Σ(체결가−로트원가)×수량"),
`unit/positions/test_fifo.py` DoD("매수 10@100, 5@110, 매도 12 → 실현
(12: 10@100+2@110), 로트 잔량 3@110; 초과 매도 → `POS_NEGATIVE_QUANTITY`;
JSON 왕복").

`Lot`(수량·단가·오픈시각)은 LB-1 계약(`contracts/v1.py`)을 그대로 쓴다 —
FIFO/WEIGHTED(LB-3) 공통 표현이기 때문이다. `FillEvent`/`CostBasisResult`는
이 리프가 소비/생산하는 순수 입출력이며 계약에 없다 — 계좌 통화(`Money`)나
포트는 이 계층에 들어오지 않는다(환산은 LB-4 `fx.py`/`pnl.py`의 책임).

`FifoLots`는 로트 큐를 들고 있는 가변 상태다("큐"라는 이름 그대로 매수는
꼬리에 push, 매도는 머리부터 소진) — 이 패키지의 다른 리프(`balance_rules`
등)가 즉값 반환형 순수 함수를 선호하는 것과 달리, 저널 fold 순서대로
체결을 연속 적용하는 것이 이 타입의 존재 이유이므로 의도적으로 가변이다.
I/O는 하지 않는다 — 시각·통화 변환·영속화는 모두 호출자 책임.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import Lot


class NegativeQuantityError(Exception):
    """`POS_NEGATIVE_QUANTITY` — 보유 로트 수량 합보다 큰 매도. 현물
    공매도 금지 — 재시도 불가, 주문 경로 버그."""


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")
    return value


@dataclass(frozen=True, slots=True)
class FillEvent:
    """FIFO 큐에 적용할 체결 하나. `price`는 계좌 통화 `Money`가 아니라
    원시 `Decimal`이다 — 원가법은 통화를 모른다."""

    side: OrderSide
    quantity: Decimal
    price: Decimal
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_aware_utc(self.occurred_at)
        if self.quantity <= 0:
            raise ValueError(f"quantity는 양수여야 합니다: {self.quantity}")


@dataclass(frozen=True, slots=True)
class CostBasisResult:
    """`apply` 한 번의 결과. `realized_pnl`은 이번 체결에서만 발생한
    실현손익이고(누적 아님), `lots`는 적용 후 큐 전체 스냅샷이다."""

    realized_pnl: Decimal
    lots: tuple[Lot, ...]


class FifoLots:
    """FIFO 로트 큐. 매수마다 `Lot`을 꼬리에 push, 매도는 머리(가장 오래된
    로트)부터 소진한다."""

    def __init__(self, lots: tuple[Lot, ...] = ()) -> None:
        self._lots: list[Lot] = list(lots)

    @property
    def lots(self) -> tuple[Lot, ...]:
        return tuple(self._lots)

    def apply(self, fill: FillEvent) -> CostBasisResult:
        if fill.side is OrderSide.BUY:
            return self._apply_buy(fill)
        return self._apply_sell(fill)

    def _apply_buy(self, fill: FillEvent) -> CostBasisResult:
        self._lots.append(
            Lot(quantity=fill.quantity, unit_cost=fill.price, opened_at=fill.occurred_at)
        )
        return CostBasisResult(realized_pnl=Decimal("0"), lots=self.lots)

    def _apply_sell(self, fill: FillEvent) -> CostBasisResult:
        available = sum((lot.quantity for lot in self._lots), Decimal("0"))
        if fill.quantity > available:
            raise NegativeQuantityError(
                f"매도 수량({fill.quantity})이 보유 로트 합({available})을 초과합니다."
            )

        remaining = fill.quantity
        realized = Decimal("0")
        new_lots: list[Lot] = []
        for lot in self._lots:
            if remaining <= 0:
                new_lots.append(lot)
                continue
            consumed = min(lot.quantity, remaining)
            realized += (fill.price - lot.unit_cost) * consumed
            remaining -= consumed
            if consumed < lot.quantity:
                new_lots.append(lot.model_copy(update={"quantity": lot.quantity - consumed}))
        self._lots = new_lots
        return CostBasisResult(realized_pnl=realized, lots=self.lots)

    def to_json(self) -> str:
        return json.dumps([lot.model_dump(mode="json") for lot in self._lots])

    @classmethod
    def from_json(cls, data: str) -> FifoLots:
        raw = json.loads(data)
        return cls(tuple(Lot.model_validate(item) for item in raw))
