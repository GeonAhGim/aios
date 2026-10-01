from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.exceptions import CurrencyMismatchError
from src.data.models import trading as trading_module
from src.data.models.base import AssetClass, Currency, Money, OptionType
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


def _order_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        client_order_id="c-neg",
        strategy_id="strat-1",
        strategy_version="v1.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )
    kwargs.update(overrides)
    return kwargs


def test_order_missing_required_asset_class_raises() -> None:
    kwargs = _order_kwargs()
    del kwargs["asset_class"]
    with pytest.raises(ValidationError):
        Order(**kwargs)


def test_order_invalid_side_enum_value_raises() -> None:
    with pytest.raises(ValidationError):
        Order(**_order_kwargs(side="NOT_A_SIDE"))


def test_order_non_decimal_quantity_raises() -> None:
    with pytest.raises(ValidationError):
        Order(**_order_kwargs(quantity="not-a-number"))


def test_money_add_currency_mismatch_raises() -> None:
    usdt = _money("100", Currency.USDT)
    krw = _money("100", Currency.KRW)
    with pytest.raises(CurrencyMismatchError):
        usdt + krw


def test_order_construction_fails_when_clock_raises() -> None:
    """실패주입: created_at default_factory가 의존하는 datetime.now가 예외를
    던지면 생성 자체가 실패해야 한다 — 부분 초기화된 Order가 조용히 반환되면
    안 된다."""

    class _BoomDatetime:
        @staticmethod
        def now(_tz: object = None) -> None:
            raise RuntimeError("clock unavailable")

    with patch.object(trading_module, "datetime", _BoomDatetime):
        with pytest.raises(RuntimeError):
            Order(**_order_kwargs())


@pytest.mark.perf
def test_order_construction_perf_under_budget(perf_budget) -> None:
    """성능단언: Order 생성 1000회가 1초 예산 내에 끝나야 한다
    (pydantic validation 회귀로 인한 급격한 저하 탐지)."""

    def _run() -> None:
        for i in range(1000):
            Order(**_order_kwargs(client_order_id=f"c-perf-{i}"))

    perf_budget.assert_within(_run, budget_ms=1000.0, label="construct 1000 Orders")


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
        asset_class=AssetClass.CRYPTO,
    )
    assert order.status == OrderStatus.CREATED
    assert order.price is None
    assert order.filled_quantity == Decimal("0")
    assert order.is_liquidation is False
    assert order.option_type is None
    assert order.underlying_symbol is None


def test_order_option_fields():
    order = Order(
        client_order_id="c-2",
        strategy_id="strat-1",
        strategy_version="v1.0",
        symbol="AAPL 250117C00200000",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        asset_class=AssetClass.US_EQUITY,
        option_type=OptionType.CALL,
        strike_price=Decimal("200"),
        expiry_date=date(2025, 1, 17),
        underlying_symbol="AAPL",
    )
    assert order.option_type == OptionType.CALL
    assert order.strike_price == Decimal("200")
    assert order.underlying_symbol == "AAPL"


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
        asset_class=AssetClass.CRYPTO,
    )
    assert position.current_price.currency == Currency.USDT
    assert position.leverage == Decimal("1")
    assert position.asset_class == AssetClass.CRYPTO


def test_account_balance_holds_arbitrary_asset_quantity():
    # AccountBalance는 Money가 아니라 Decimal — asset이 임의 코인일 수 있어
    # Currency enum(USDT/KRW)으로 표현 불가하기 때문(01번 §1.4 v1.4 정정).
    balance = AccountBalance(
        exchange="bitget",
        asset="BTC",
        total=Decimal("0.5"),
        available=Decimal("0.4"),
        used_margin=Decimal("0.1"),
    )
    assert balance.asset == "BTC"
    assert balance.total == Decimal("0.5")
    assert balance.used_margin == Decimal("0.1")


def test_account_balance_used_margin_defaults_to_zero():
    balance = AccountBalance(
        exchange="bitget", asset="USDT", total=Decimal("100"), available=Decimal("100")
    )
    assert balance.used_margin == Decimal("0")


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
