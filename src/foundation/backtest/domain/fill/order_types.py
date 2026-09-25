"""BT-6 — 주문유형 트리거 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-6, §3.4(`order_types: {limit, stop, oco, trailing}`).

각 함수는 "이 봉에서 트리거됐는가"만 판정한다 — 트리거된 체결가 계산은
슬리피지 모델(BT-2, `slippage.py`)의 책임이라 섞지 않는다(5개 파일은
서로 임포트하지 않는다). `OrderTypesConfig`로 꺼진 유형이 들어오면
`OrderTypeDisabledError`로 거부한다(`models_v2.py` docstring이
이 모듈에 위임한 책임).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models_v2 import OrderTypesConfig

OrderTypeName = Literal["limit", "stop", "oco", "trailing"]


class OrderTypeDisabledError(ValueError):
    """`OrderTypesConfig`에서 꺼진 유형으로 들어온 주문."""


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def ensure_order_type_enabled(config: OrderTypesConfig, order_type: OrderTypeName) -> None:
    enabled = {
        "limit": config.limit,
        "stop": config.stop,
        "oco": config.oco,
        "trailing": config.trailing,
    }[order_type]
    if not enabled:
        raise OrderTypeDisabledError(f"order_type={order_type}는 비활성화된 설정이다")


def is_limit_triggered(
    *, side: OrderSide, limit_price: Decimal, bar_low: Decimal, bar_high: Decimal
) -> bool:
    """매수 지정가는 저가가 지정가 이하로 닿으면, 매도 지정가는 고가가
    지정가 이상으로 닿으면 그 봉에서 체결 가능하다고 본다."""

    _reject_negative_or_nan(limit_price, "limit_price")
    _reject_negative_or_nan(bar_low, "bar_low")
    _reject_negative_or_nan(bar_high, "bar_high")
    if side == OrderSide.BUY:
        return bar_low <= limit_price
    return bar_high >= limit_price


def is_stop_triggered(
    *, side: OrderSide, stop_price: Decimal, bar_low: Decimal, bar_high: Decimal
) -> bool:
    """매수 스탑(숏 커버·돌파매수)은 고가가 스탑 이상으로 닿으면, 매도
    스탑(손절)은 저가가 스탑 이하로 닿으면 트리거된다."""

    _reject_negative_or_nan(stop_price, "stop_price")
    _reject_negative_or_nan(bar_low, "bar_low")
    _reject_negative_or_nan(bar_high, "bar_high")
    if side == OrderSide.BUY:
        return bar_high >= stop_price
    return bar_low <= stop_price


@dataclass(frozen=True, slots=True)
class OcoResolution:
    triggered_leg: Literal["a", "b", "none"]
    cancelled_leg: Literal["a", "b", "none"]


def resolve_oco(
    *, leg_a_triggered: bool, leg_b_triggered: bool, priority_leg: Literal["a", "b"]
) -> OcoResolution:
    """한쪽이 체결되면 반대편은 즉시 취소된다.

    같은 봉 안에서 둘 다 닿는 경우(고저 범위가 두 레그를 모두 포함하는
    큰 봉)는 실거래소에서 어느 쪽이 실제로 먼저 체결됐는지 이 정보만으로는
    알 수 없다 — 이 모호함을 조용히 숨기지 않고, 호출자가 `priority_leg`로
    보수적 가정(예: 손절 레그 우선)을 명시적으로 고르게 강제한다.
    """

    if leg_a_triggered and leg_b_triggered:
        if priority_leg == "a":
            return OcoResolution(triggered_leg="a", cancelled_leg="b")
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    if leg_a_triggered:
        return OcoResolution(triggered_leg="a", cancelled_leg="b")
    if leg_b_triggered:
        return OcoResolution(triggered_leg="b", cancelled_leg="a")
    return OcoResolution(triggered_leg="none", cancelled_leg="none")


@dataclass(frozen=True, slots=True)
class TrailingStopState:
    extreme_price: Decimal
    stop_price: Decimal


def update_trailing_stop(
    *,
    side: OrderSide,
    state: TrailingStopState,
    bar_low: Decimal,
    bar_high: Decimal,
    trail_pct: Decimal,
) -> TrailingStopState:
    """`side=SELL`(롱 포지션 청산용 트레일링 스탑)은 신고가가 나올 때마다
    극값을 올리고 스탑을 `극값 * (1 - trail_pct)`로 끌어올린다.
    `side=BUY`(숏 포지션 청산용)는 신저가를 따라 스탑을
    `극값 * (1 + trail_pct)`로 끌어내린다 — 극값은 불리한 방향으로는
    절대 되돌리지 않는다(단조).
    """

    _reject_negative_or_nan(bar_low, "bar_low")
    _reject_negative_or_nan(bar_high, "bar_high")
    _reject_negative_or_nan(trail_pct, "trail_pct")
    _reject_negative_or_nan(state.extreme_price, "state.extreme_price")

    if side == OrderSide.SELL:
        new_extreme = max(state.extreme_price, bar_high)
        new_stop = new_extreme * (Decimal(1) - trail_pct)
    else:
        new_extreme = min(state.extreme_price, bar_low)
        new_stop = new_extreme * (Decimal(1) + trail_pct)
    return TrailingStopState(extreme_price=new_extreme, stop_price=new_stop)


@dataclass(frozen=True, slots=True)
class OcaResolution:
    """N-way generalization of `OcoResolution` -- task-2623 bracket exit
    (profit/loss/trail legs). Isomorphic to the live path's
    `src/services/oms/domain/order_types/oca.py::resolve_oca` (same
    signature, same decision rule) -- parity between the two is asserted by
    `tests/unit/oms/test_bracket_oca_parity.py`, not by importing one from
    the other (foundation/backtest and services/oms are separate bounded
    contexts, deliberately duplicated the same way `resolve_oco` already
    is on both sides)."""

    triggered_leg: str | None
    cancelled_legs: tuple[str, ...]


def resolve_oca(*, triggered: Mapping[str, bool], priority_order: Sequence[str]) -> OcaResolution:
    """Bracket exit is an OCA (one-cancels-all) group of up to 3 legs
    (`profit`/`loss`/`trail`). The first leg in `priority_order` that is
    triggered wins; every other leg in the group is cancelled -- including
    legs that also triggered on the same bar (the ambiguous-tie case
    `resolve_oco` already documents: caller states a conservative
    assumption via ordering instead of this module guessing). No leg
    triggered -> `triggered_leg=None`, nothing cancelled yet."""

    if not priority_order:
        raise ValueError("priority_order는 최소 1개 레그가 필요하다")
    if set(triggered) != set(priority_order):
        raise ValueError("triggered와 priority_order의 레그 집합이 일치해야 한다")
    for leg in priority_order:
        if triggered[leg]:
            cancelled = tuple(other for other in priority_order if other != leg)
            return OcaResolution(triggered_leg=leg, cancelled_legs=cancelled)
    return OcaResolution(triggered_leg=None, cancelled_legs=())


def bracket_quantity_for_fill(*, requested_qty: Decimal, filled_qty: Decimal) -> Decimal:
    """Bracket exit legs must never cover more than what the entry actually
    filled (partial-fill quantity parity) -- if the entry only partially
    fills, the bracket's exit legs are sized to `filled_qty`, never the
    originally requested quantity. Isomorphic to the live-side function of
    the same name in `src/services/oms/domain/order_types/oca.py`."""

    _reject_negative_or_nan(requested_qty, "requested_qty")
    _reject_negative_or_nan(filled_qty, "filled_qty")
    return min(requested_qty, filled_qty)
