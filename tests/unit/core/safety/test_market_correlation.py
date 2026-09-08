from decimal import Decimal

from src.core.safety.market_correlation import is_market_wide_move


def test_basket_below_min_symbols_is_undeterminable():
    result = is_market_wide_move(
        {"BTC/USDT": Decimal("-5"), "ETH/USDT": Decimal("-5")},
        account_loss_pct=Decimal("8"),
        min_symbols=3,
        move_threshold_pct=Decimal("3"),
    )
    assert result is None


def test_no_account_loss_is_never_correlated():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-9"), "C": Decimal("-9")},
        account_loss_pct=Decimal("0"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_majority_basket_decline_is_market_wide():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-4"), "C": Decimal("-3.5"), "D": Decimal("0.2")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is True


def test_single_symbol_decline_is_isolated_not_market_wide():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("0.1"), "C": Decimal("0.2"), "D": Decimal("-0.5")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_exactly_half_declined_is_not_majority():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-9"), "C": Decimal("0"), "D": Decimal("0")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_decline_exactly_at_threshold_counts():
    result = is_market_wide_move(
        {"A": Decimal("-3"), "B": Decimal("-3"), "C": Decimal("0")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is True
