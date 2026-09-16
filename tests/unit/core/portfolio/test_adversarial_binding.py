"""L4_strategy_portfolio_backtest_v1.0.md#section 6 row 586 -- mandate_binding.py
adversarial tests.

DoD (e) must be falsifiable: a mandate view whose `forbidden_assets` was
wiped after the `revision_hash` was computed must never reach an approvable
`BindingResult`, and none of negative price / zero equity / NaN inputs may
raise an unhandled exception stack -- they must all end in a deterministic
denial instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.portfolio.mandate_binding import (
    POLICY_FORBIDDEN_ASSET,
    POLICY_MAX_SINGLE_INSTRUMENT,
    BindingResult,
    MandateRevisionHashMismatchError,
    bind,
)
from src.core.portfolio.state_input import PortfolioAggregate
from tests.unit.core.portfolio.test_mandate_binding import agg, mandate

_NAN = Decimal("NaN")


# --- revision_hash tamper detection -------------------------------------------


def test_wiped_forbidden_assets_with_stale_hash_is_rejected():
    original = mandate(forbidden_assets=["SANCTIONED/USDT"])
    tampered = original.model_copy(update={"forbidden_assets": []})
    assert tampered.revision_hash == original.revision_hash  # hash was NOT recomputed

    with pytest.raises(MandateRevisionHashMismatchError):
        bind(
            qty=Decimal("1"),
            price=Decimal("100"),
            symbol="SANCTIONED/USDT",
            agg=agg(),
            mandate=tampered,
        )


def test_tampered_numeric_limit_with_stale_hash_is_rejected():
    original = mandate(max_single_instrument_pct=20.0)
    tampered = original.model_copy(update={"max_single_instrument_pct": 100.0})
    assert tampered.revision_hash == original.revision_hash

    with pytest.raises(MandateRevisionHashMismatchError):
        bind(
            qty=Decimal("1"),
            price=Decimal("100"),
            symbol="BTC/USDT",
            agg=agg(),
            mandate=tampered,
        )


def test_correctly_hashed_mandate_is_not_rejected_by_the_hash_check():
    # Control case: a mandate that was NOT tampered with must pass the hash
    # check (proves the check isn't vacuously rejecting everything).
    result = bind(
        qty=Decimal("1"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("100000")),
        mandate=mandate(),
    )
    assert result.denied is False


# --- extreme values deny instead of raising -----------------------------------


def test_negative_price_denies_instead_of_approving_or_raising():
    result = bind(
        qty=Decimal("10"),
        price=Decimal("-100"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


def test_zero_price_denies_instead_of_dividing_by_zero():
    result = bind(
        qty=Decimal("10"),
        price=Decimal("0"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_zero_equity_denies_instead_of_dividing_by_zero():
    result = bind(
        qty=Decimal("10"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("0")),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_negative_equity_denies_instead_of_approving():
    result = bind(
        qty=Decimal("10"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("-10000")),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_nan_quantity_denies_instead_of_raising_invalid_operation():
    result = bind(
        qty=_NAN,
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_nan_price_denies_instead_of_raising_invalid_operation():
    result = bind(
        qty=Decimal("10"),
        price=_NAN,
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_nan_total_equity_denies_instead_of_raising_invalid_operation():
    # `PortfolioAggregate`'s own validation already rejects NaN on normal
    # construction (pydantic's `finite_number` check) -- `model_construct`
    # bypasses that to prove `bind()` still fails closed even if a corrupted
    # aggregate reached it some other way (e.g. deserialized from storage).
    corrupted = PortfolioAggregate.model_construct(
        total_equity=_NAN,
        per_symbol_pct={},
        per_strategy_pct={},
        total_exposure_pct=Decimal("0"),
        cash_pct=Decimal("100"),
        as_of=agg().as_of,
    )
    result = bind(
        qty=Decimal("10"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=corrupted,
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_negative_or_zero_quantity_denies_instead_of_slipping_through():
    result = bind(
        qty=Decimal("0"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


# --- D2 실패 주입 (failure injection) -----------------------------------------


class _BrokenForbiddenAssetsFeed:
    """Stand-in for a corrupted mandate view whose `forbidden_assets`
    iterable breaks partway through (e.g. a lazily-materialized set from a
    mandate-store snapshot read that lost its connection) instead of
    yielding a clean, complete list."""

    def __iter__(self):
        yield "SANCTIONED/USDT"
        raise RuntimeError("upstream mandate store disconnected mid-read")


def test_bind_propagates_broken_forbidden_assets_source_failure():
    """`bind()` must let a broken `forbidden_assets` iterable's exception
    propagate (fail-closed) instead of silently swallowing it and treating
    the mandate as if it had no forbidden assets."""
    original = mandate(forbidden_assets=["SANCTIONED/USDT"])
    broken = original.model_copy(update={"forbidden_assets": _BrokenForbiddenAssetsFeed()})

    with pytest.raises(RuntimeError, match="disconnected mid-read"):
        bind(
            qty=Decimal("1"),
            price=Decimal("100"),
            symbol="SANCTIONED/USDT",
            agg=agg(),
            mandate=broken,
        )


# --- D2 게이트 적색 재현 (gate-red reproduction) --------------------------------


def _regressed_bind_without_hash_guard(
    qty: Decimal,
    price: Decimal,
    symbol: str,
    mandate,
) -> BindingResult:
    """Mirrors `bind()`'s forbidden-asset check but with the
    `revision_hash` integrity check removed -- the regression this test
    guards against (INVARIANTS.md I-07: an approval gate's hard-fail
    condition must actually be able to return FAIL)."""
    if symbol in mandate.forbidden_assets:
        return BindingResult(quantity=Decimal("0"), reasons=[POLICY_FORBIDDEN_ASSET], denied=True)
    return BindingResult(quantity=qty, reasons=[], denied=False)


def test_missing_hash_guard_would_let_a_wiped_forbidden_asset_trade_through():
    """적색: `revision_hash` 무결성 검사가 빠지면, 발급 이후 `forbidden_assets`가
    비워진(위조된) 매니데이트가 제재 종목 거래를 그대로 승인해 버린다(I-07
    위반 -- 게이트가 FAIL을 반환할 수 없게 된다).
    녹색: 실제 `bind()`는 같은 입력을 `MandateRevisionHashMismatchError`로
    fail-closed 거부한다."""
    original = mandate(forbidden_assets=["SANCTIONED/USDT"])
    tampered = original.model_copy(update={"forbidden_assets": []})
    assert tampered.revision_hash == original.revision_hash  # hash was NOT recomputed

    # 적색: 가드가 없으면 제재 종목 거래가 그대로 승인된다.
    regressed_result = _regressed_bind_without_hash_guard(
        qty=Decimal("1"), price=Decimal("100"), symbol="SANCTIONED/USDT", mandate=tampered
    )
    assert regressed_result.denied is False

    # 녹색: 실제 구현은 같은 입력을 정의된 예외로 거부한다.
    with pytest.raises(MandateRevisionHashMismatchError):
        bind(
            qty=Decimal("1"),
            price=Decimal("100"),
            symbol="SANCTIONED/USDT",
            agg=agg(),
            mandate=tampered,
        )
