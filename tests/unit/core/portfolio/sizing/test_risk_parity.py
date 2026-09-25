"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 93, §8 line 572 — risk_parity.py tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingInputMissingError
from src.core.portfolio.sizing.risk_parity import existing_block_weight_fraction, size
from src.core.portfolio.state_input import PortfolioAggregate, PortfolioStateInput

_HEX64 = "d" * 64
_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.RISK_PARITY,
        "min_trade_notional": Decimal("10"),
        "cost_model": CostModelRef(model_id="cm-1", cost_model_hash=_HEX64),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def _exposures(total_exposure_pct: Decimal = Decimal("60")) -> PortfolioAggregate:
    return PortfolioAggregate(
        total_equity=Decimal("10000"),
        per_symbol_pct={"BTC/USDT": total_exposure_pct},
        per_strategy_pct={"strat-existing": total_exposure_pct},
        total_exposure_pct=total_exposure_pct,
        cash_pct=Decimal("100") - total_exposure_pct,
        as_of=_AS_OF,
    )


def _inp(**overrides: Any) -> PortfolioStateInput:
    base: dict[str, Any] = {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9000"),
        "realized_vol_pct": Decimal("25"),
        "exposures": _exposures(),
        "portfolio_config": _config(),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


def test_size_matches_exact_decimal_formula():
    # inv_vol=1/25=0.04, existing=0.6, denom=0.64 -> weight=0.04/0.64=0.0625
    result = size(_inp())

    assert result.weight_pct == Decimal("6.25")
    assert result.quantity == Decimal("0.0125")  # 10000*0.0625/50000
    assert result.method == SizingMethod.RISK_PARITY


def test_weight_plus_existing_block_sums_to_exactly_one():
    inp = _inp()
    new_fraction = size(inp).weight_pct / Decimal("100")
    existing_fraction = existing_block_weight_fraction(inp)

    assert new_fraction + existing_fraction == Decimal("1")


def test_lower_vol_yields_higher_weight_than_higher_vol():
    low_vol = size(_inp(realized_vol_pct=Decimal("10")))
    high_vol = size(_inp(realized_vol_pct=Decimal("40")))

    assert low_vol.weight_pct > high_vol.weight_pct


def test_size_rejects_none_exposures():
    with pytest.raises(SizingInputMissingError) as exc_info:
        size(_inp(exposures=None))
    assert exc_info.value.field == "exposures"


def test_size_rejects_none_realized_vol_pct():
    with pytest.raises(SizingInputMissingError):
        size(_inp(realized_vol_pct=None))


class _RaisingExposures:
    """실행 조회 대역이 손상돼 `total_exposure_pct` 하이드레이션에 실패하는
    지연 프록시를 흉내낸다(예: L18 aggregate()의 캐시된 조회 대역 오류)."""

    @property
    def total_exposure_pct(self) -> Decimal:
        raise RuntimeError("exposures feed corrupted mid-read")


def test_size_propagates_exposures_feed_failure_without_swallowing():
    """`exposures`가 `None`이 아니라 통과하지만(그래서 `require()`는 걸러내지
    못한다), 실제 필드 접근에서 손상된 조회 대역이 예외를 던지면 `size()`의
    어떤 코드 경로도 그것을 삼키고 기본값(0 비중 등)으로 대체하지 않는다 —
    fail-closed로 그대로 전파해야 한다."""
    inp = _inp()
    inp.exposures = _RaisingExposures()

    with pytest.raises(RuntimeError, match="exposures feed corrupted"):
        size(inp)
