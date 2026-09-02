"""Backtest domain/rules.py 단위테스트 — DB 없이 순수 함수만 검증."""
from decimal import Decimal

from src.foundation.backtest.domain.models import CostModel
from src.foundation.backtest.domain.rules import (
    has_enough_warmup,
    is_look_ahead_safe,
    warn_if_zero_cost,
)


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
