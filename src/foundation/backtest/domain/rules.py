"""Backtest Simulation Engine 순수 규칙 함수 — DB/HTTP 없이 단위테스트 가능해야 한다.

Spec: AIOSproject 109번 §5 — look-ahead bias 방지가 이 엔진의 핵심 불변조건이다.
docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L29 (rules.py 확장 행) --
`assert_fill_after_signal`/`require_cost_model` 추가분.
"""
from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from src.foundation.backtest.domain.models import CostModel


@runtime_checkable
class _HasBarIndex(Protocol):
    """`OrderEvent`/`FillEvent`(L30 domain/events.py, 아직 미구현) 둘 다 만족할
    최소 구조 -- I2를 확인하려면 `bar_index`만 있으면 충분해, 아직 배선되지
    않은 이벤트 모듈에 이 순수 규칙이 의존하지 않게 한다."""

    bar_index: int


class LookaheadViolationError(ValueError):
    """`BACKTEST_LOOKAHEAD_VIOLATION` -- 주문(`order_ev`)보다 나중 bar가
    아닌 곳에서 체결(`fill_ev`)이 발생했다(I2 위반)."""

    error_code: ClassVar[str] = "BACKTEST_LOOKAHEAD_VIOLATION"

    def __init__(self, *, signal_bar_index: int, fill_bar_index: int) -> None:
        self.signal_bar_index = signal_bar_index
        self.fill_bar_index = fill_bar_index
        super().__init__(
            f"{self.error_code}: fill_bar_index={fill_bar_index} is not "
            f"after signal_bar_index={signal_bar_index}"
        )


class CostModelRequiredError(ValueError):
    """`VALIDATION_COST_MODEL_REQUIRED` -- `allow_zero=False`인데 비용모델의
    모든 항목이 0이다(46번 §2 "cost model absent = hard fail")."""

    error_code: ClassVar[str] = "VALIDATION_COST_MODEL_REQUIRED"


def is_look_ahead_safe(*, signal_bar_index: int, fill_bar_index: int) -> bool:
    """신호가 발생한 bar의 정보로 같은 bar나 과거 bar에 체결시키면
    미래 정보를 쓴 것이다(look-ahead bias) — 반드시 signal_bar_index보다
    나중 bar에서만 체결된다."""
    return fill_bar_index > signal_bar_index


def warn_if_zero_cost(cost_model: CostModel) -> str | None:
    """0 비용 자체를 금지하지는 않는다(의도적으로 비용을 배제한 민감도
    분석 시나리오가 있을 수 있음 — 46번 §2 Robustness 행) — 다만 사용자가
    "비용 없는 수익만 제시" 함정에 빠지지 않도록 결과에 경고를 남긴다."""
    if cost_model.fee_bps == 0 and cost_model.slippage_bps == 0:
        return (
            "cost_model이 fee_bps=0, slippage_bps=0입니다 — 이 결과의 수익률은 "
            "거래비용을 전혀 반영하지 않았습니다(46번 §2 Backtest 행 필수 공시)."
        )
    return None


def has_enough_warmup(*, total_bars: int, warmup_bars: int) -> bool:
    """warmup 구간을 빼고 나면 평가할 bar가 하나도 안 남는 설정을
    조용히 통과시키지 않는다."""
    return total_bars > warmup_bars


def assert_fill_after_signal(order_ev: _HasBarIndex, fill_ev: _HasBarIndex) -> None:
    """I2를 hard fail로 강제한다 -- `is_look_ahead_safe`가 `bool`을 돌려주고
    호출자가 그 결과를 판단하는 것과 달리, 이 함수는 재생 루프 한복판에서
    위반을 조용히 지나칠 수 없도록 예외로 끊는다."""
    if not is_look_ahead_safe(
        signal_bar_index=order_ev.bar_index, fill_bar_index=fill_ev.bar_index
    ):
        raise LookaheadViolationError(
            signal_bar_index=order_ev.bar_index, fill_bar_index=fill_ev.bar_index
        )


def require_cost_model(cost_model: CostModel, *, allow_zero: bool) -> None:
    """`warn_if_zero_cost`는 0비용을 허용하되 경고만 남기지만, 이 함수는
    `allow_zero=False`인 호출자(기본 검증 정책, 46번 §2)에게 0비용을
    hard fail로 거부한다 -- 두 함수는 같은 조건을 서로 다른 엄격도로 쓴다."""
    if allow_zero:
        return
    if cost_model.fee_bps == 0 and cost_model.slippage_bps == 0:
        raise CostModelRequiredError(
            f"{CostModelRequiredError.error_code}: fee_bps=0, slippage_bps=0이고 "
            "allow_zero=False -- 비용모델 없는 결과는 거부한다"
        )
