"""L4_strategy_portfolio_backtest_v1.0.md#§2 rows 88~89, §9 L17 — contract tests.

DoD (a)-(f) must all be falsifiable: dropping a single field, using the
wrong type, or breaking a value rule must fail some test in this file.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.state_input import PortfolioAggregate, PortfolioStateInput

_HEX64 = "a" * 64


def _cost_model_ref() -> CostModelRef:
    return CostModelRef(model_id="cm-1", cost_model_hash=_HEX64)


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.FIXED_FRACTIONAL,
        "min_trade_notional": Decimal("10"),
        "cost_model": _cost_model_ref(),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


# --- (a) 1:1 field match against the §2 spec table -------------------------


def test_portfolio_config_has_exactly_the_spec_fields():
    expected = {
        "schema_version",
        "method",
        "fraction_pct",
        "target_vol_pct",
        "kelly_cap_pct",
        "rebalance_band_pct",
        "min_trade_notional",
        "cost_model",
    }
    assert set(PortfolioConfig.model_fields.keys()) == expected


def test_sizing_method_has_exactly_the_spec_members():
    assert {m.value for m in SizingMethod} == {
        "FIXED_FRACTIONAL",
        "VOLATILITY_TARGET",
        "KELLY_CAPPED",
        "RISK_PARITY",
    }


def test_portfolio_config_schema_version_and_defaults():
    cfg = _config()
    assert cfg.schema_version == "pcfg-v1"
    assert cfg.fraction_pct == Decimal("100")
    assert cfg.rebalance_band_pct == Decimal("5")
    assert cfg.target_vol_pct is None
    assert cfg.kelly_cap_pct is None


def test_portfolio_state_input_has_exactly_the_spec_fields():
    expected = {
        "schema_version",
        "allocated_capital",
        "position_quantity",
        "current_price",
        "total_equity",
        "cash_available",
        "realized_vol_pct",
        "win_rate",
        "avg_win_loss_ratio",
        "exposures",
        "mandate",
        "portfolio_config",
    }
    assert set(PortfolioStateInput.model_fields.keys()) == expected


def test_portfolio_state_input_optional_fields_default_to_none():
    inp = PortfolioStateInput(
        allocated_capital=Decimal("1000"),
        position_quantity=Decimal("0"),
        current_price=Decimal("50000"),
        total_equity=Decimal("10000"),
        cash_available=Decimal("9000"),
        portfolio_config=_config(),
    )
    assert inp.realized_vol_pct is None
    assert inp.win_rate is None
    assert inp.avg_win_loss_ratio is None
    assert inp.exposures is None
    assert inp.mandate is None
    assert inp.schema_version == "psi-v1"


# --- (b) config_hash() stability --------------------------------------------


def test_config_hash_stable_for_equal_values():
    a = _config(rebalance_band_pct=Decimal("5"))
    b = _config(rebalance_band_pct=Decimal("5"))
    assert a.config_hash() == b.config_hash()


def test_config_hash_changes_when_rebalance_band_pct_changes():
    a = _config(rebalance_band_pct=Decimal("5"))
    b = _config(rebalance_band_pct=Decimal("6"))
    assert a.config_hash() != b.config_hash()


# --- (c) dict acceptance -----------------------------------------------------


def _legacy_state_dict() -> dict[str, object]:
    # Carries the existing PortfolioEngine.allocate() current_portfolio_state
    # keys (allocated_capital/position_quantity/current_price/total_equity)
    # as-is, plus the new required fields (cash_available, portfolio_config).
    return {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0.5"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9500"),
        "portfolio_config": _config().model_dump(mode="python"),
    }


def test_from_dict_accepts_legacy_shaped_dict():
    inp = PortfolioStateInput.from_dict(_legacy_state_dict())
    assert inp.allocated_capital == Decimal("1000")
    assert inp.total_equity == Decimal("10000")
    assert inp.portfolio_config.method == SizingMethod.FIXED_FRACTIONAL


def test_from_dict_rejects_dict_missing_total_equity():
    data = _legacy_state_dict()
    del data["total_equity"]
    with pytest.raises(ValidationError):
        PortfolioStateInput.from_dict(data)


# --- (d) Decimal enforced + negative rejected --------------------------------


def test_min_trade_notional_rejects_negative():
    with pytest.raises(ValidationError):
        _config(min_trade_notional=Decimal("-1"))


def test_portfolio_config_rejects_float_for_decimal_field():
    with pytest.raises(ValidationError):
        _config(min_trade_notional=1.5)


def test_portfolio_state_input_rejects_float_for_decimal_field():
    with pytest.raises(ValidationError):
        PortfolioStateInput(
            allocated_capital=1000.0,
            position_quantity=Decimal("0"),
            current_price=Decimal("50000"),
            total_equity=Decimal("10000"),
            cash_available=Decimal("9000"),
            portfolio_config=_config(),
        )


# --- (e) CostModelRef is a minimal reference type -----------------------------


def test_cost_model_ref_is_a_minimal_reference_not_a_cost_model_reimplementation():
    assert set(CostModelRef.model_fields.keys()) == {"model_id", "cost_model_hash"}


def test_cost_model_ref_rejects_non_hex_hash():
    with pytest.raises(ValidationError):
        CostModelRef(model_id="cm-1", cost_model_hash="not-a-hash")


# --- exposures: PortfolioAggregate round-trip ---------------------------------


def test_portfolio_state_input_accepts_exposures_aggregate():
    agg = PortfolioAggregate(
        total_equity=Decimal("10000"),
        per_symbol_pct={"BTC/USDT": Decimal("10")},
        per_strategy_pct={"strat-1": Decimal("10")},
        total_exposure_pct=Decimal("10"),
        cash_pct=Decimal("90"),
        as_of=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    inp = PortfolioStateInput(
        allocated_capital=Decimal("1000"),
        position_quantity=Decimal("0"),
        current_price=Decimal("50000"),
        total_equity=Decimal("10000"),
        cash_available=Decimal("9000"),
        exposures=agg,
        portfolio_config=_config(),
    )
    assert inp.exposures is not None
    assert inp.exposures.total_equity == Decimal("10000")
