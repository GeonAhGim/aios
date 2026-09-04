"""BT-3 — 수수료 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-3, §3.4(`commission: VenueTier{venue, maker_bps, taker_bps, min_fee}`).

`VenueTierCommission`(`domain/models_v2.py`)을 그대로 소비한다. 정액
하한(`min_fee`)은 비율 수수료가 그보다 작을 때 올림 적용한다(내림 아님)
— 소액 체결에서 거래소가 실제로 부과하는 최소 수수료를 반영한다.
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.backtest.domain.models_v2 import VenueTierCommission

_BPS = Decimal("10000")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def compute_commission(model: VenueTierCommission, *, is_maker: bool, notional: Decimal) -> Decimal:
    """체결 명목가(`notional` = 체결가 * 수량)에 등급별 bps를 적용하고
    `min_fee` 하한을 강제한다."""

    _reject_negative_or_nan(notional, "notional")
    rate_bps = model.maker_bps if is_maker else model.taker_bps
    fee = notional * rate_bps / _BPS
    return max(fee, model.min_fee)
