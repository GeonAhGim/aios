from decimal import Decimal

from src.core.validator.order_validator import validate_order_params
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType


def _order(**overrides) -> Order:
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


def test_valid_market_order_passes():
    result = validate_order_params(_order())
    assert result.is_valid is True
    assert result.errors == []


def test_quantity_zero_or_negative_rejected():
    result = validate_order_params(_order(quantity=Decimal("0")))
    assert result.is_valid is False
    assert any("수량" in e for e in result.errors)


def test_limit_order_without_price_rejected():
    result = validate_order_params(_order(order_type=OrderType.LIMIT))
    assert result.is_valid is False
    assert any("LIMIT" in e for e in result.errors)


def test_market_order_with_price_rejected():
    price = Money(amount=Decimal("100"), currency=Currency.USDT)
    result = validate_order_params(_order(order_type=OrderType.MARKET, price=price))
    assert result.is_valid is False
    assert any("MARKET" in e for e in result.errors)


def test_price_not_multiple_of_tick_size_rejected():
    price = Money(amount=Decimal("100.003"), currency=Currency.USDT)
    result = validate_order_params(
        _order(order_type=OrderType.LIMIT, price=price), tick_size=Decimal("0.01")
    )
    assert result.is_valid is False
    assert any("tick_size" in e for e in result.errors)


def test_price_multiple_of_tick_size_passes():
    price = Money(amount=Decimal("100.05"), currency=Currency.USDT)
    result = validate_order_params(
        _order(order_type=OrderType.LIMIT, price=price), tick_size=Decimal("0.01")
    )
    assert result.is_valid is True


def test_unsupported_asset_class_rejected():
    result = validate_order_params(
        _order(asset_class=AssetClass.KR_OPTION),
        supported_asset_classes=[AssetClass.CRYPTO],
    )
    assert result.is_valid is False
    assert any("UNSUPPORTED_ASSET_CLASS" in e for e in result.errors)


def test_supported_asset_class_passes():
    result = validate_order_params(
        _order(asset_class=AssetClass.CRYPTO),
        supported_asset_classes=[AssetClass.CRYPTO, AssetClass.KR_EQUITY],
    )
    assert result.is_valid is True
