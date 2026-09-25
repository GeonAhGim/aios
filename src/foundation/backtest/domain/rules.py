"""Backtest Simulation Engine — pure rule functions, no DB/HTTP, must be unit-testable in isolation.

Spec: AIOSproject #109 §5 — preventing look-ahead bias is the core invariant of this engine.
docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L29 (rules.py extension row) --
the `assert_fill_after_signal`/`require_cost_model` addition.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from src.foundation.backtest.domain.models import CostModel


@runtime_checkable
class _HasBarIndex(Protocol):
    """The minimal structure both `OrderEvent` and `FillEvent`
    (domain/events.py) satisfy -- checking I2 only needs `bar_index`, so this
    pure rule keeps a narrow structural dependency instead of importing the
    event module directly. Declared as a read-only property (not a plain
    field) because both events are frozen pydantic models -- a plain
    `bar_index: int` member would demand a settable attribute and reject
    every frozen model under mypy's structural check."""

    @property
    def bar_index(self) -> int: ...


class LookaheadViolationError(ValueError):
    """`BACKTEST_LOOKAHEAD_VIOLATION` -- the fill (`fill_ev`) happened at a
    bar that is not after the order's (`order_ev`) bar (I2 violation)."""

    error_code: ClassVar[str] = "BACKTEST_LOOKAHEAD_VIOLATION"

    def __init__(self, *, signal_bar_index: int, fill_bar_index: int) -> None:
        self.signal_bar_index = signal_bar_index
        self.fill_bar_index = fill_bar_index
        super().__init__(
            f"{self.error_code}: fill_bar_index={fill_bar_index} is not "
            f"after signal_bar_index={signal_bar_index}"
        )


class CostModelRequiredError(ValueError):
    """`VALIDATION_COST_MODEL_REQUIRED` -- `allow_zero=False` but every field
    of the cost model is 0 (standard 46 §2, "cost model absent = hard fail")."""

    error_code: ClassVar[str] = "VALIDATION_COST_MODEL_REQUIRED"


def is_look_ahead_safe(*, signal_bar_index: int, fill_bar_index: int) -> bool:
    """Filling at the same bar or a past bar using information from the signal bar
    constitutes look-ahead bias — the fill must occur at a bar strictly after
    signal_bar_index."""
    return fill_bar_index > signal_bar_index


def warn_if_zero_cost(cost_model: CostModel) -> str | None:
    """Zero cost itself is not prohibited (sensitivity-analysis scenarios may
    intentionally exclude costs — Standard #46 §2 Robustness row) — however,
    a warning is left so users do not fall into the "profit with no cost" trap."""
    if cost_model.fee_bps == 0 and cost_model.slippage_bps == 0:
        return (
            "cost_model이 fee_bps=0, slippage_bps=0입니다 — 이 결과의 수익률은 "
            "거래비용을 전혀 반영하지 않았습니다(46번 §2 Backtest 행 필수 공시)."
        )
    return None


def has_enough_warmup(*, total_bars: int, warmup_bars: int) -> bool:
    """Does not silently pass a configuration where removing the warmup
    interval leaves zero bars to evaluate."""
    return total_bars > warmup_bars


def assert_fill_after_signal(order_ev: _HasBarIndex, fill_ev: _HasBarIndex) -> None:
    """Enforces I2 as a hard fail -- unlike `is_look_ahead_safe`, which
    returns a `bool` for the caller to judge, this function raises so a
    violation cannot silently pass through the middle of a replay loop."""
    if not is_look_ahead_safe(
        signal_bar_index=order_ev.bar_index, fill_bar_index=fill_ev.bar_index
    ):
        raise LookaheadViolationError(
            signal_bar_index=order_ev.bar_index, fill_bar_index=fill_ev.bar_index
        )


def require_cost_model(cost_model: CostModel, *, allow_zero: bool) -> None:
    """`warn_if_zero_cost` allows zero cost but only leaves a warning, while
    this function rejects zero cost as a hard fail for callers that pass
    `allow_zero=False` (the default validation policy, standard 46 §2) --
    the two functions apply the same condition at different strictness."""
    if allow_zero:
        return
    if cost_model.fee_bps == 0 and cost_model.slippage_bps == 0:
        raise CostModelRequiredError(
            f"{CostModelRequiredError.error_code}: fee_bps=0, slippage_bps=0이고 "
            "allow_zero=False -- 비용모델 없는 결과는 거부한다"
        )
