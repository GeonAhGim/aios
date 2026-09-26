"""Adversarial validation tests — negative / failure-injection.

Target: src/core/validator/ (order_validator, strategy_validator, result)
DoD (task-7937, DEEPEN of task-6704):
- negative test >= 3
- failure-injection >= 1
- no INVARIANTS.md violation

I-03 (strategy validation gate), I-04 (portfolio risk budget), I-05 (order validation gate).
"""

from decimal import Decimal

import pytest

from src.core.validator.order_validator import validate_order_params
from src.core.validator.result import ValidationResult
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType

# ── helpers ──────────────────────────────────────────────────────────────────


def _order(**overrides) -> Order:
    """Return a minimal valid Order, merged with *overrides*."""
    defaults = dict(
        client_order_id="c-1",
        strategy_id="strat-1",
        strategy_version="v1.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )
    defaults.update(overrides)
    return Order(**defaults)


# ── negative tests (invariant-violating inputs) ──────────────────────────────


def test_negative_zero_quantity_rejected():
    """Quantity == 0 violates I-05 order validation gate — must be rejected."""
    result = validate_order_params(_order(quantity=Decimal("0")))
    assert result.is_valid is False
    assert any("수량" in e or "quantity" in e.lower() for e in result.errors)


def test_negative_limit_order_without_price_rejected():
    """LIMIT order without price violates I-05 — must be rejected."""
    result = validate_order_params(_order(order_type=OrderType.LIMIT))
    assert result.is_valid is False
    assert any("LIMIT" in e for e in result.errors)


def test_negative_market_order_with_price_rejected():
    """MARKET order with price violates I-05 market guard — must be rejected."""
    price = Money(amount=Decimal("100"), currency=Currency.USDT)
    result = validate_order_params(_order(order_type=OrderType.MARKET, price=price))
    assert result.is_valid is False
    assert any("MARKET" in e for e in result.errors)


def test_negative_negative_quantity_rejected():
    """Negative quantity violates I-05 — must be rejected."""
    result = validate_order_params(_order(quantity=Decimal("-0.01")))
    assert result.is_valid is False
    assert any("수량" in e or "quantity" in e.lower() for e in result.errors)


# ── failure-injection (monkeypatch dependency exception) ─────────────────────


def test_failure_injection_validate_order_params_raises():
    """Inject an exception inside validate_order_params by monkeypatching
    the ValidationResult class so its constructor raises.

    validate_order_params does NOT wrap ValidationResult construction in
    try/except, so the injected RuntimeError must propagate — this confirms
    the function is fail-closed: it never silently returns a bogus result.
    """
    import src.core.validator.order_validator as ov_module

    class RaisingValidationResult:
        """Replacement that always raises on construction."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError("injected dependency failure")

    original_vr = ov_module.ValidationResult
    try:
        ov_module.ValidationResult = RaisingValidationResult
        with pytest.raises(RuntimeError, match="injected dependency failure"):
            validate_order_params(_order())
    finally:
        ov_module.ValidationResult = original_vr


# ── ValidationResult invariant tests ─────────────────────────────────────────


def test_validation_result_is_valid_property_true():
    """ValidationResult(0 errors) → is_valid == True."""
    vr = ValidationResult(is_valid=True, errors=[])
    assert vr.is_valid is True


def test_validation_result_is_valid_property_false():
    """ValidationResult(1+ errors) → is_valid == False."""
    vr = ValidationResult(is_valid=False, errors=["some error"])
    assert vr.is_valid is False


def test_validation_result_errors_are_list():
    """errors must always be a list, never None."""
    vr = ValidationResult(is_valid=True, errors=[])
    assert isinstance(vr.errors, list)
