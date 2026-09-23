"""BT-2 — Slippage model (pure domain).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-2, §3.4(`SlippageModel`: Fixed|Percent|VolumeImpact).

Consumes `BacktestConfigV2.slippage` (`domain/models_v2.py`) as-is —
does not override contract fields here. Returns a single "adverse-shifted
fill price" (effective price); position/cash updates after fill are outside
this module's responsibility (assembly is BT-10 `application/quick_backtest.py`).

Unverified: `VolumeImpactSlippage`'s impact formula (`impact = k * participation`,
`participation = min(quantity / bar_volume, participation_cap)`) has not been
calibrated against specific exchange microstructure data — we explicitly adopt
the assumption of linear proportionality to participation (§3.4 defines fields
only and leaves the formula open).
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
    """Return the filled price shifted adversely from `reference_price`
    in the `side` direction — buy pays more, sell receives less."""

    _reject_negative_or_nan(reference_price, "reference_price")
    _reject_negative_or_nan(quantity, "quantity")
    direction = Decimal(1) if side == OrderSide.BUY else Decimal(-1)

    if isinstance(model, FixedSlippage):
        offset_fraction = model.bps / _BPS
    elif isinstance(model, PercentSlippage):
        offset_fraction = model.pct
    elif isinstance(model, VolumeImpactSlippage):
        offset_fraction = _volume_impact_fraction(model, quantity=quantity, bar_volume=bar_volume)
    else:  # pragma: no cover - path blocked by discriminator union
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
