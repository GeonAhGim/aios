"""시뮬 수수료 모델(L4 명세 §2-F).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F, §9 L4-22.

순수 도메인. 수수료 = 체결가 × 수량 × bps / 10000, 통화는 `fee_currency`
(quote 통화 기준 — 거래소별 base 통화 차감 수수료는 모델링하지 않음, 미검증).
라운딩은 ROUND_UP(수수료가 트레이더에게 유리하게 깎이지 않음).
음수 bps(리베이트)는 거부한다 — 시뮬 낙관 금지.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from src.data.models.base import Currency, Money
from src.exchanges.paper.fill_model import SimFill

_BPS = Decimal("10000")
_FEE_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class FeeModel:
    maker_bps: Decimal
    taker_bps: Decimal
    fee_currency: Currency

    def __post_init__(self) -> None:
        if self.maker_bps < 0 or self.taker_bps < 0:
            raise ValueError("maker_bps/taker_bps는 0 이상이어야 합니다(리베이트 미지원).")

    def fee(self, fill: SimFill) -> Money:
        if fill.quantity <= 0 or fill.price <= 0:
            raise ValueError("fee: 체결 수량·가격은 양수여야 합니다.")
        bps = self.maker_bps if fill.liquidity == "MAKER" else self.taker_bps
        amount = (fill.price * fill.quantity * bps / _BPS).quantize(
            _FEE_QUANTUM, rounding=ROUND_UP
        )
        return Money(amount=amount, currency=self.fee_currency)
