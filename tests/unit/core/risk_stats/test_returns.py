"""L4_risk_and_safety_v1.0.md#R-18 — returns.py 순수 함수 테스트."""
from decimal import Decimal

import numpy as np
import pytest

from src.core.risk_stats.returns import bars_per_day, log_returns, scale_sigma


def test_bars_per_day_1m_is_1440():
    assert bars_per_day("1m") == 1440


def test_bars_per_day_1d_is_1():
    assert bars_per_day("1d") == 1


def test_bars_per_day_unsupported_timeframe_raises():
    with pytest.raises(ValueError):
        bars_per_day("7m")


def test_log_returns_length_is_n_minus_1():
    closes = [Decimal("100"), Decimal("101"), Decimal("99"), Decimal("102")]
    result = log_returns(closes)
    assert len(result) == len(closes) - 1


def test_log_returns_known_value():
    closes = [Decimal("100"), Decimal("110")]
    result = log_returns(closes)
    assert result == pytest.approx([np.log(1.1)])


def test_log_returns_single_close_returns_empty():
    result = log_returns([Decimal("100")])
    assert len(result) == 0


def test_scale_sigma_includes_bars_per_day_factor():
    # R4 회귀 방지: bars_per_day가 곱해지지 않으면 이 값과 달라진다.
    assert scale_sigma(0.01, bars_per_day=1440, horizon_days=1) == pytest.approx(
        0.01 * (1440**0.5)
    )


def test_scale_sigma_daily_bars_horizon_one_is_identity():
    assert scale_sigma(0.02, bars_per_day=1, horizon_days=1) == pytest.approx(0.02)
