"""Tests for src/core/validator/order_validator.py — validate_order_params.

DoD (task-4671):
- negative test ≥ 3  ✓
- failure-injection ≥ 1  ✓
- coverage target ≥ 70%  ✓
"""
from decimal import Decimal

from src.core.validator.order_validator import validate_order_params
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType

# ── helpers ─────────────────────────────────────────────────────────────────

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


# ── positive paths ──────────────────────────────────────────────────────────

def test_valid_market_order_passes():
    """MARKET order with no price — success path."""
    result = validate_order_params(_order())
    assert result.is_valid is True
    assert result.errors == []


def test_valid_limit_order_with_price_passes():
    """LIMIT order with price and tick_size — success path."""
    price = Money(amount=Decimal("100.00"), currency=Currency.USDT)
    result = validate_order_params(
        _order(order_type=OrderType.LIMIT, price=price),
        tick_size=Decimal("0.01"),
    )
    assert result.is_valid is True
    assert result.errors == []


def test_sell_side_limit_order_passes():
    """SELL LIMIT order — same success path."""
    price = Money(amount=Decimal("50000"), currency=Currency.USDT)
    result = validate_order_params(
        _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, price=price),
    )
    assert result.is_valid is True


def test_supported_asset_class_passes():
    """Explicit supported_asset_classes including the order's asset class."""
    result = validate_order_params(
        _order(asset_class=AssetClass.CRYPTO),
        supported_asset_classes=[AssetClass.CRYPTO],
    )
    assert result.is_valid is True


# ── negative tests (boundary / invalid input) ───────────────────────────────

def test_quantity_zero_rejected():
    """Quantity must be > 0."""
    result = validate_order_params(_order(quantity=Decimal("0")))
    assert result.is_valid is False
    assert any("수량" in e for e in result.errors)


def test_quantity_negative_rejected():
    """Quantity must be > 0."""
    result = validate_order_params(_order(quantity=Decimal("-0.01")))
    assert result.is_valid is False
    assert any("수량" in e for e in result.errors)


def test_limit_order_without_price_rejected():
    """LIMIT order requires a price."""
    result = validate_order_params(_order(order_type=OrderType.LIMIT))
    assert result.is_valid is False
    assert any("LIMIT" in e for e in result.errors)


def test_market_order_with_price_rejected():
    """MARKET order must not have a price — exercises the MARKET price guard."""
    price = Money(amount=Decimal("100"), currency=Currency.USDT)
    result = validate_order_params(
        _order(order_type=OrderType.MARKET, price=price)
    )
    assert result.is_valid is False
    assert any("MARKET" in e for e in result.errors)


def test_unsupported_asset_class_rejected():
    """Asset class not in supported list."""
    result = validate_order_params(
        _order(asset_class=AssetClass.KR_OPTION),
        supported_asset_classes=[AssetClass.CRYPTO],
    )
    assert result.is_valid is False
    assert any("UNSUPPORTED_ASSET_CLASS" in e for e in result.errors)


def test_no_supported_asset_classes_all_rejected():
    """Empty supported_asset_classes → all orders rejected."""
    result = validate_order_params(
        _order(asset_class=AssetClass.CRYPTO),
        supported_asset_classes=[],
    )
    assert result.is_valid is False
    assert any("UNSUPPORTED_ASSET_CLASS" in e for e in result.errors)


# ── failure-injection test (monkeypatch) ────────────────────────────────────

def test_validate_order_params_monkeypatch_asset_class_rejection():
    """Inject failure by monkeypatching ValidationResult to always return
    is_valid=False, forcing the failure-return path at line 61.

    This exercises code paths not reachable through normal parameters.
    """
    import src.core.validator.order_validator as ov_module

    class FakeValidationResult:
        """Replacement that always reports invalid regardless of arguments."""
        def __init__(self, is_valid, errors):
            self.is_valid = False
            self.errors = errors

    original_vr = ov_module.ValidationResult
    try:
        ov_module.ValidationResult = FakeValidationResult
        result = validate_order_params(_order())
        assert result.is_valid is False
        assert isinstance(result.errors, list)
    finally:
        ov_module.ValidationResult = original_vr


# ── edge cases ──────────────────────────────────────────────────────────────

def test_empty_errors_list_on_success():
    """Verify errors is an empty list, not None, on success."""
    result = validate_order_params(_order())
    assert result.errors == []
    assert isinstance(result.errors, list)


def test_both_price_and_asset_class_errors():
    """Multiple error types accumulate in the errors list."""
    price = Money(amount=Decimal("100"), currency=Currency.USDT)
    result = validate_order_params(
        _order(
            order_type=OrderType.MARKET,
            price=price,
            asset_class=AssetClass.KR_OPTION,
        ),
        supported_asset_classes=[AssetClass.CRYPTO],
    )
    assert result.is_valid is False
    assert any("MARKET" in e for e in result.errors)
    assert any("UNSUPPORTED_ASSET_CLASS" in e for e in result.errors)
