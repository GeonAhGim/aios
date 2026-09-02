"""109번 §5 — 실제 거래소 호출 대신 bar OHLC로 체결가를 결정한다.

호출자는 반드시 "신호가 나온 bar의 **다음** bar"를 `bar`로 넘겨야 한다 —
같은 bar의 종가로 체결시키면 그 bar가 끝나야 알 수 있는 정보(종가)로
그 bar 안에서 거래한 것이 되어 look-ahead bias다(domain/rules.py의
`is_look_ahead_safe`가 이 불변조건을 검증한다). 체결가는 그 다음 bar의
시가(open)를 쓴다 — 실무에서 가장 흔히 쓰이는 보수적 가정.
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import CostModel, SimulatedFill

_BPS = Decimal("10000")


def simulate_fill(
    *,
    bar: Candle,
    bar_index: int,
    side: OrderSide,
    quantity: Decimal,
    cost_model: CostModel,
) -> SimulatedFill:
    base_price = bar.open
    slippage_direction = 1 if side == OrderSide.BUY else -1
    effective_price = base_price * (
        Decimal(1) + slippage_direction * cost_model.slippage_bps / _BPS
    )
    slippage_cost = abs(effective_price - base_price) * quantity
    fee = effective_price * quantity * cost_model.fee_bps / _BPS

    return SimulatedFill(
        bar_index=bar_index,
        timestamp=bar.open_time,
        symbol=bar.symbol,
        side=side,
        price=effective_price,
        quantity=quantity,
        fee=fee,
        slippage_cost=slippage_cost,
    )
