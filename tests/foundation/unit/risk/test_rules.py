"""U-15 personal-conservative 주문 리스크 규칙 단위테스트 — DB 없음."""

from __future__ import annotations

from decimal import Decimal

from src.foundation.risk.domain.models import PersonalRiskBundle
from src.foundation.risk.domain.rules import (
    OrderRiskCheckInput,
    PersonalRiskViolation,
    evaluate_personal_order,
)

_BUNDLE = PersonalRiskBundle(
    name="personal-conservative",
    position_pct_of_equity=Decimal("0.02"),
    daily_loss_kill_pct=Decimal("0.03"),
    max_exposure_pct=Decimal("0.30"),
    default_notional_cap_krw=Decimal("1000000"),
    symbol_whitelist=frozenset({"BTC/USDT"}),
    exchange_notional_caps={},
)


def _order(**overrides: object) -> OrderRiskCheckInput:
    defaults: dict[str, object] = {
        "exchange": "bitget",
        "symbol": "BTC/USDT",
        "order_notional_krw": Decimal("10000"),
        "account_equity_krw": Decimal("1000000"),
        "current_exposure_krw": Decimal("0"),
        "daily_realized_pnl_pct": Decimal("0"),
    }
    defaults.update(overrides)
    return OrderRiskCheckInput(**defaults)  # type: ignore[arg-type]


def test_allowed_order_within_all_limits():
    decision = evaluate_personal_order(_BUNDLE, _order())

    assert decision.allowed is True
    assert decision.violations == ()
    assert decision.should_kill is False


def test_rejects_symbol_not_whitelisted():
    decision = evaluate_personal_order(_BUNDLE, _order(symbol="DOGE/USDT"))

    assert decision.allowed is False
    assert PersonalRiskViolation.SYMBOL_NOT_WHITELISTED in decision.violations


def test_empty_whitelist_rejects_every_symbol():
    empty_whitelist_bundle = PersonalRiskBundle(
        name="personal-conservative",
        position_pct_of_equity=Decimal("0.02"),
        daily_loss_kill_pct=Decimal("0.03"),
        max_exposure_pct=Decimal("0.30"),
        default_notional_cap_krw=Decimal("1000000"),
        symbol_whitelist=frozenset(),
        exchange_notional_caps={},
    )

    decision = evaluate_personal_order(empty_whitelist_bundle, _order())

    assert decision.allowed is False
    assert PersonalRiskViolation.SYMBOL_NOT_WHITELISTED in decision.violations


def test_rejects_position_size_over_2pct_of_equity():
    decision = evaluate_personal_order(
        _BUNDLE,
        _order(order_notional_krw=Decimal("30000"), account_equity_krw=Decimal("1000000")),
    )

    assert decision.allowed is False
    assert PersonalRiskViolation.POSITION_SIZE_EXCEEDED in decision.violations


def test_rejects_notional_over_exchange_cap():
    decision = evaluate_personal_order(
        _BUNDLE,
        _order(
            order_notional_krw=Decimal("2000000"),
            account_equity_krw=Decimal("100000000"),
        ),
    )

    assert decision.allowed is False
    assert PersonalRiskViolation.NOTIONAL_CAP_EXCEEDED in decision.violations


def test_rejects_exposure_over_30pct_max():
    decision = evaluate_personal_order(
        _BUNDLE,
        _order(
            order_notional_krw=Decimal("10000"),
            current_exposure_krw=Decimal("400000"),
            account_equity_krw=Decimal("1000000"),
        ),
    )

    assert decision.allowed is False
    assert PersonalRiskViolation.EXPOSURE_LIMIT_EXCEEDED in decision.violations


def test_daily_loss_at_kill_threshold_triggers_kill_and_rejects():
    decision = evaluate_personal_order(_BUNDLE, _order(daily_realized_pnl_pct=Decimal("-0.03")))

    assert decision.allowed is False
    assert decision.should_kill is True
    assert PersonalRiskViolation.DAILY_LOSS_LIMIT_BREACHED in decision.violations


def test_daily_loss_below_threshold_does_not_kill():
    decision = evaluate_personal_order(_BUNDLE, _order(daily_realized_pnl_pct=Decimal("-0.01")))

    assert decision.should_kill is False


def test_zero_equity_is_rejected_without_division_by_zero():
    decision = evaluate_personal_order(_BUNDLE, _order(account_equity_krw=Decimal("0")))

    assert decision.allowed is False
    assert decision.violations == (PersonalRiskViolation.ACCOUNT_STATE_INVALID,)


def test_negative_equity_is_rejected():
    decision = evaluate_personal_order(_BUNDLE, _order(account_equity_krw=Decimal("-1")))

    assert decision.allowed is False
    assert decision.violations == (PersonalRiskViolation.ACCOUNT_STATE_INVALID,)
