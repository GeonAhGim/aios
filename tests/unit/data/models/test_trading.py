from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.base import Currency, Money
from src.data.models.trading import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SecretBundle,
)


def _money(amount: str, currency: Currency = Currency.USDT) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def test_order_defaults_and_market_price_none():
    order = Order(
        client_order_id="c-1",
        strategy_id="strat-1",
        strategy_version="v1.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
    )
    assert order.status == OrderStatus.CREATED
    assert order.price is None
    assert order.filled_quantity == Decimal("0")
    assert order.is_liquidation is False


def test_position_requires_money_fields():
    position = Position(
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id="strat-1",
        quantity=Decimal("0.5"),
        average_entry_price=_money("60000"),
        current_price=_money("65000"),
        unrealized_pnl=_money("2500"),
        realized_pnl=_money("0"),
        entry_time=datetime.now(timezone.utc),
    )
    assert position.current_price.currency == Currency.USDT
    assert position.leverage == Decimal("1")


def test_account_balance_currency_specific():
    balance = AccountBalance(
        exchange="kis",
        asset="KRW",
        total=_money("1000000", Currency.KRW),
        available=_money("900000", Currency.KRW),
        used_margin=_money("100000", Currency.KRW),
    )
    assert balance.total.currency == Currency.KRW


def test_secret_bundle_repr_masks_values():
    bundle = SecretBundle(
        database_url="postgresql+asyncpg://user:password@localhost/db",
        jwt_secret_key="super-secret",
        credential_encryption_key="key",
        bitget_api_key="k",
        bitget_api_secret="s",
        kis_app_key="k",
        kis_app_secret="s",
    )
    assert "super-secret" not in repr(bundle)
    assert "fields, masked" in repr(bundle)
