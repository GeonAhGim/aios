"""BT-2 — 슬리피지 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-2, §3.4(`SlippageModel`: Fixed|Percent|VolumeImpact).

`BacktestConfigV2.slippage`(`domain/models_v2.py`)를 그대로 소비한다 —
계약 필드를 여기서 재정의하지 않는다. 반환값은 "불리한 방향으로 밀린
체결가"(effective price) 하나뿐이다 — 체결 이후 포지션/현금 갱신은 이
모듈의 책임이 아니다(조립은 BT-10 `application/quick_backtest.py`).

미검증: `VolumeImpactSlippage`의 충격식(`impact = k * participation`,
`participation = min(quantity / bar_volume, participation_cap)`)은 특정
거래소 마이크로구조 데이터로 보정되지 않았다 — participation에 선형
비례한다는 가정만 명시적으로 채택한다(§3.4는 필드만 정의하고 산식은
정하지 않았다).
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models_v2 import (
    FixedSlippage,
    PercentSlippage,
    SlippageModel,
    VolumeImpactSlippage,
)

_BPS = Decimal("10000")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def apply_slippage(
    model: SlippageModel,
    *,
    side: OrderSide,
    reference_price: Decimal,
    quantity: Decimal,
    bar_volume: Decimal | None = None,
) -> Decimal:
    """`reference_price`(슬리피지 적용 전 기준가)에서 `side` 방향으로
    불리하게 밀린 체결가를 반환한다 — 매수는 비싸게, 매도는 싸게."""

    _reject_negative_or_nan(reference_price, "reference_price")
    _reject_negative_or_nan(quantity, "quantity")
    direction = Decimal(1) if side == OrderSide.BUY else Decimal(-1)

    if isinstance(model, FixedSlippage):
        offset_fraction = model.bps / _BPS
    elif isinstance(model, PercentSlippage):
        offset_fraction = model.pct
    elif isinstance(model, VolumeImpactSlippage):
        offset_fraction = _volume_impact_fraction(model, quantity=quantity, bar_volume=bar_volume)
    else:  # pragma: no cover - 판별 유니온(discriminator)이 막는 경로
        raise ValueError(f"알 수 없는 슬리피지 모델: {model!r}")

    return reference_price * (Decimal(1) + direction * offset_fraction)


def _volume_impact_fraction(
    model: VolumeImpactSlippage, *, quantity: Decimal, bar_volume: Decimal | None
) -> Decimal:
    if bar_volume is None:
        raise ValueError("VolumeImpactSlippage는 bar_volume 없이 계산할 수 없다")
    _reject_negative_or_nan(bar_volume, "bar_volume")
    if bar_volume == 0:
        raise ValueError("bar_volume=0이면 참여율(participation)을 계산할 수 없다")
    participation = min(quantity / bar_volume, model.participation_cap)
    return model.k * participation
