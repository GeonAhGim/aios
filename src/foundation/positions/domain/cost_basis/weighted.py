"""LB-3 — 가중평균 원가법(weighted average).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3
(`domain/cost_basis/weighted.py`: "가중평균: 매수 시 평단 재계산, 매도 시
평단 유지"), `unit/positions/test_weighted.py` DoD("평단 재계산, 매도 시
평단 불변").

`FillEvent`/`CostBasisResult`/`NegativeQuantityError`는 [[fifo]]의 계약을
그대로 재사용한다 — FIFO/WEIGHTED는 같은 입출력 표현을 공유하는 서로 다른
로트 관리 전략일 뿐이다(중복 정의 금지). `Lot`은 LB-1 계약
(`contracts/v1.py`)을 그대로 쓴다.

가중평균은 로트를 여러 개 쌓지 않고 포지션 전체를 단일 평단으로 뭉친다 —
`lots`는 항상 0개(무포지션) 또는 1개(단일 블렌디드 로트)다. 매수 시
평단 = (기존수량×기존평단 + 체결수량×체결가) / (기존수량+체결수량)을
§3.4 정밀도(`NUMERIC(30,10)`, `Decimal("1e-10")`, `ROUND_HALF_EVEN`)로
quantize한다. 매도는 평단을 바꾸지 않고 실현손익 = (체결가−평단)×수량만
계산한다 — 초과 매도는 FIFO와 동일하게 `NegativeQuantityError`. 순수
도메인(I/O import 0) — 시각·통화 변환·영속화는 호출자 책임.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import Lot
from src.foundation.positions.domain.cost_basis.fifo import (
    CostBasisResult,
    FillEvent,
    NegativeQuantityError,
)

__all__ = ["WeightedAverage", "FillEvent", "CostBasisResult", "NegativeQuantityError"]

_PRICE_QUANTUM = Decimal("1e-10")


class WeightedAverage:
    """가중평균 원가법. 매수마다 평단을 재계산하고, 매도는 평단을 유지한
    채 실현손익만 뽑아낸다."""

    def __init__(self, lot: Lot | None = None) -> None:
        self._lot = lot

    @property
    def lots(self) -> tuple[Lot, ...]:
        return () if self._lot is None else (self._lot,)

    def apply(self, fill: FillEvent) -> CostBasisResult:
        if fill.side is OrderSide.BUY:
            return self._apply_buy(fill)
        return self._apply_sell(fill)

    def _apply_buy(self, fill: FillEvent) -> CostBasisResult:
        if self._lot is None:
            new_quantity = fill.quantity
            new_unit_cost = fill.price.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
            opened_at = fill.occurred_at
        else:
            new_quantity = self._lot.quantity + fill.quantity
            blended = (
                self._lot.quantity * self._lot.unit_cost + fill.quantity * fill.price
            ) / new_quantity
            new_unit_cost = blended.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
            opened_at = self._lot.opened_at
        self._lot = Lot(quantity=new_quantity, unit_cost=new_unit_cost, opened_at=opened_at)
        return CostBasisResult(realized_pnl=Decimal("0"), lots=self.lots)

    def _apply_sell(self, fill: FillEvent) -> CostBasisResult:
        available = Decimal("0") if self._lot is None else self._lot.quantity
        if fill.quantity > available:
            raise NegativeQuantityError(
                f"매도 수량({fill.quantity})이 보유 수량({available})을 초과합니다."
            )
        assert self._lot is not None  # available > 0이면 _lot은 반드시 존재한다

        realized = (fill.price - self._lot.unit_cost) * fill.quantity
        remaining_quantity = self._lot.quantity - fill.quantity
        if remaining_quantity == 0:
            self._lot = None
        else:
            self._lot = self._lot.model_copy(update={"quantity": remaining_quantity})
        return CostBasisResult(realized_pnl=realized, lots=self.lots)
