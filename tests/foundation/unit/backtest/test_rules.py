"""Backtest domain/rules.py 단위테스트 — DB 없이 순수 함수만 검증."""
from dataclasses import dataclass
from decimal import Decimal

import pytest

from src.foundation.backtest.domain.models import CostModel
from src.foundation.backtest.domain.rules import (
    CostModelRequiredError,
    LookaheadViolationError,
    assert_fill_after_signal,
    has_enough_warmup,
    is_look_ahead_safe,
    require_cost_model,
    warn_if_zero_cost,
)


@dataclass
class _FakeEvent:
    bar_index: int


def test_is_look_ahead_safe_rejects_same_bar_fill() -> None:
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=5) is False


def test_is_look_ahead_safe_rejects_earlier_bar_fill() -> None:
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=4) is False


def test_is_look_ahead_safe_accepts_next_bar_fill() -> None:
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=6) is True


def test_warn_if_zero_cost_warns_on_fully_zero_model() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert warn_if_zero_cost(cost_model) is not None


def test_warn_if_zero_cost_silent_when_fee_present() -> None:
    cost_model = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("0"))
    assert warn_if_zero_cost(cost_model) is None


def test_has_enough_warmup_rejects_when_no_bars_left() -> None:
    assert has_enough_warmup(total_bars=20, warmup_bars=20) is False


def test_has_enough_warmup_accepts_when_bars_remain() -> None:
    assert has_enough_warmup(total_bars=21, warmup_bars=20) is True


def test_assert_fill_after_signal_raises_on_same_bar_fill() -> None:
    order_ev = _FakeEvent(bar_index=5)
    fill_ev = _FakeEvent(bar_index=5)
    with pytest.raises(LookaheadViolationError) as exc_info:
        assert_fill_after_signal(order_ev, fill_ev)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_assert_fill_after_signal_passes_on_next_bar_fill() -> None:
    order_ev = _FakeEvent(bar_index=5)
    fill_ev = _FakeEvent(bar_index=6)
    assert assert_fill_after_signal(order_ev, fill_ev) is None


def test_require_cost_model_rejects_all_zero_when_disallowed() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    with pytest.raises(CostModelRequiredError) as exc_info:
        require_cost_model(cost_model, allow_zero=False)
    assert exc_info.value.error_code == "VALIDATION_COST_MODEL_REQUIRED"


def test_require_cost_model_allows_all_zero_when_permitted() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert require_cost_model(cost_model, allow_zero=True) is None
