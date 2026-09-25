"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 96, section 9 L20 --
mandate_binding.py tests.

DoD (a)-(g) must all be falsifiable: FORBIDDEN passing as a clamp instead of
a denial, an off-by-one clamp value, a clamp-to-zero silently approving a
zero-quantity order, or reasons ordering drifting between two identical
calls must each fail some test here.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.portfolio.mandate_binding import (
    POLICY_FORBIDDEN_ASSET,
    POLICY_MAX_SINGLE_INSTRUMENT,
    POLICY_MAX_TOTAL_EXPOSURE,
    POLICY_MIN_CASH_BUFFER,
    bind,
)
from src.core.portfolio.state_input import PortfolioAggregate
from src.foundation.mandates.contracts.v1 import (
    Autonomy,
    MandateRevisionState,
    MandateRevisionView,
)

_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _revision_hash(
    *,
    max_total_exposure_pct: float,
    max_single_instrument_pct: float,
    min_cash_buffer_pct: float,
    max_daily_loss_pct: float,
    allowed_autonomy: Autonomy,
    forbidden_assets: list[str],
) -> str:
    payload = "|".join(
        [
            f"{max_total_exposure_pct:.2f}",
            f"{max_single_instrument_pct:.2f}",
            f"{min_cash_buffer_pct:.2f}",
            f"{max_daily_loss_pct:.2f}",
            allowed_autonomy.value,
            ",".join(sorted(forbidden_assets)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mandate(
    *,
    max_total_exposure_pct: float = 100.0,
    max_single_instrument_pct: float = 100.0,
    min_cash_buffer_pct: float = 0.0,
    max_daily_loss_pct: float = 100.0,
    allowed_autonomy: Autonomy = Autonomy.PAPER,
    forbidden_assets: list[str] | None = None,
) -> MandateRevisionView:
    forbidden = forbidden_assets or []
    return MandateRevisionView(
        id=uuid4(),
        mandate_id=uuid4(),
        revision_no=1,
        state=MandateRevisionState.ACTIVE,
        max_total_exposure_pct=max_total_exposure_pct,
        max_single_instrument_pct=max_single_instrument_pct,
        min_cash_buffer_pct=min_cash_buffer_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        allowed_autonomy=allowed_autonomy,
        forbidden_assets=forbidden,
        revision_hash=_revision_hash(
            max_total_exposure_pct=max_total_exposure_pct,
            max_single_instrument_pct=max_single_instrument_pct,
            min_cash_buffer_pct=min_cash_buffer_pct,
            max_daily_loss_pct=max_daily_loss_pct,
            allowed_autonomy=allowed_autonomy,
            forbidden_assets=forbidden,
        ),
        cooling_off_started_at=None,
        created_at=_AS_OF,
        activated_at=_AS_OF,
    )


def agg(
    *,
    total_equity: Decimal = Decimal("10000"),
    per_symbol_pct: dict[str, Decimal] | None = None,
    total_exposure_pct: Decimal = Decimal("0"),
    cash_pct: Decimal = Decimal("100"),
) -> PortfolioAggregate:
    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct or {},
        per_strategy_pct={},
        total_exposure_pct=total_exposure_pct,
        cash_pct=cash_pct,
        as_of=_AS_OF,
    )


# --- (b) FORBIDDEN_ASSET denies, never clamps --------------------------------


def test_forbidden_asset_denies_exactly():
    result = bind(
        qty=Decimal("10"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_FORBIDDEN_ASSET]


# --- (c) single-instrument clamp exact value ---------------------------------


def test_single_instrument_clamp_exact_value():
    # total_equity 10000, price 250, requested qty 10 (=2500, 25%) against a
    # 20% single-instrument limit -> allowed notional 2000 -> qty 2000/250=8.
    result = bind(
        qty=Decimal("10"),
        price=Decimal("250"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("10000")),
        mandate=mandate(max_single_instrument_pct=20.0),
    )
    assert result.denied is False
    assert result.quantity == Decimal("8")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


def test_single_instrument_clamp_accounts_for_existing_position():
    # Already holding 15% of equity in this symbol; limit is 20% -> only 5%
    # (500 notional) of new room remains -> qty 500/100=5.
    result = bind(
        qty=Decimal("50"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("10000"), per_symbol_pct={"BTC/USDT": Decimal("15")}),
        mandate=mandate(max_single_instrument_pct=20.0),
    )
    assert result.denied is False
    assert result.quantity == Decimal("5")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


# --- total-exposure and cash-buffer clamps -----------------------------------


def test_total_exposure_clamp_exact_value():
    # 30% total-exposure limit already at 20% existing -> 10% (1000) of new
    # room -> qty 1000/100=10. Single-instrument limit is set loose (100%)
    # so it never binds here.
    result = bind(
        qty=Decimal("50"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("10000"), total_exposure_pct=Decimal("20")),
        mandate=mandate(max_total_exposure_pct=30.0),
    )
    assert result.denied is False
    assert result.quantity == Decimal("10")
    assert result.reasons == [POLICY_MAX_TOTAL_EXPOSURE]


def test_min_cash_buffer_clamp_exact_value():
    # cash is 100% of equity, buffer requires 80% remain -> only 20% (2000)
    # may be spent -> qty 2000/100=20.
    result = bind(
        qty=Decimal("50"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("10000"), cash_pct=Decimal("100")),
        mandate=mandate(min_cash_buffer_pct=80.0),
    )
    assert result.denied is False
    assert result.quantity == Decimal("20")
    assert result.reasons == [POLICY_MIN_CASH_BUFFER]


# --- (c)/(f) sequential order is fixed and observable ------------------------


def test_sequential_clamp_order_is_single_then_total_then_cash():
    """All three constraints bind at once, each strictly tighter than the
    last (single 30 -> total 25 -> cash 20 shares). Because the pipeline
    always applies single-instrument first, every constraint contributes a
    reason. Reversing the order (cash first) would instead clamp straight to
    20 shares and record only `POLICY_MIN_CASH_BUFFER` -- the fixed order is
    what makes the fuller `reasons` audit trail below appear.
    """
    result = bind(
        qty=Decimal("50"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(
            total_equity=Decimal("10000"), total_exposure_pct=Decimal("0"), cash_pct=Decimal("100")
        ),
        mandate=mandate(
            max_single_instrument_pct=30.0,
            max_total_exposure_pct=25.0,
            min_cash_buffer_pct=80.0,
        ),
    )
    assert result.denied is False
    assert result.quantity == Decimal("20")
    assert result.reasons == [
        POLICY_MAX_SINGLE_INSTRUMENT,
        POLICY_MAX_TOTAL_EXPOSURE,
        POLICY_MIN_CASH_BUFFER,
    ]


# --- (d) clamp-to-zero denies, never returns a zero-quantity approval --------


def test_clamp_to_zero_denies_instead_of_approving_zero_quantity():
    result = bind(
        qty=Decimal("5"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("10000"), per_symbol_pct={"BTC/USDT": Decimal("100")}),
        mandate=mandate(max_single_instrument_pct=50.0),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


# --- (f) deterministic reasons/result across repeated calls ------------------


def test_same_inputs_produce_equal_results_twice():
    kwargs = dict(
        qty=Decimal("50"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("10000"), total_exposure_pct=Decimal("20")),
        mandate=mandate(max_total_exposure_pct=30.0),
    )
    assert bind(**kwargs) == bind(**kwargs)


# --- an unclamped order approves the full requested quantity -----------------


def test_unclamped_order_approves_full_quantity_with_no_reasons():
    result = bind(
        qty=Decimal("10"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("100000")),
        mandate=mandate(),
    )
    assert result.denied is False
    assert result.quantity == Decimal("10")
    assert result.reasons == []


# --- negative tests: invariant-violating inputs must be rejected ---------------


def test_nan_quantity_denied():
    """NaN qty triggers _is_unsafe_input → denied with MAX_SINGLE_INSTRUMENT."""
    result = bind(
        qty=Decimal("nan"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


def test_nan_price_denied():
    """NaN price triggers _is_unsafe_input → denied."""
    result = bind(
        qty=Decimal("10"),
        price=Decimal("nan"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


def test_nan_total_equity_rejected_by_portfolio_aggregate():
    """NaN total_equity is rejected by PortfolioAggregate (Pydantic finite_number
    constraint) before bind() even runs — fail-closed at the model layer."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        agg(total_equity=Decimal("nan"))


def test_zero_quantity_denied():
    """Zero qty is unsafe input → denied, never approved."""
    result = bind(
        qty=Decimal("0"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


def test_negative_price_denied():
    """Negative price is unsafe input → denied (would make qty*price look small)."""
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


def test_zero_total_equity_denied():
    """Zero total_equity is unsafe input → denied."""
    result = bind(
        qty=Decimal("10"),
        price=Decimal("100"),
        symbol="BTC/USDT",
        agg=agg(total_equity=Decimal("0")),
        mandate=mandate(),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_MAX_SINGLE_INSTRUMENT]


def test_revision_hash_mismatch_raises():
    """Tampered revision_hash raises MandateRevisionHashMismatchError — fail-closed."""
    from src.core.portfolio.mandate_binding import MandateRevisionHashMismatchError

    tampered = mandate(
        max_total_exposure_pct=100.0,
    )
    tampered.revision_hash = tampered.revision_hash + "x"
    try:
        bind(
            qty=Decimal("10"),
            price=Decimal("100"),
            symbol="BTC/USDT",
            agg=agg(),
            mandate=tampered,
        )
    except ValueError as exc:
        assert isinstance(exc, MandateRevisionHashMismatchError)
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("Expected MandateRevisionHashMismatchError on hash mismatch")


def test_forbidden_asset_precedes_all_clamps():
    """FORBIDDEN_ASSET denial happens before any clamp arithmetic — even if
    clamps would also bind, only FORBIDDEN_ASSET appears in reasons."""
    result = bind(
        qty=Decimal("1000"),
        price=Decimal("1"),
        symbol="SANCTIONED/USDT",
        agg=agg(total_equity=Decimal("10000"), per_symbol_pct={"SANCTIONED/USDT": Decimal("999")}),
        mandate=mandate(
            max_single_instrument_pct=5.0,
            max_total_exposure_pct=10.0,
            min_cash_buffer_pct=50.0,
            forbidden_assets=["SANCTIONED/USDT"],
        ),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")
    assert result.reasons == [POLICY_FORBIDDEN_ASSET]
    # Verify no clamp reason leaks into the denial
    assert POLICY_MAX_SINGLE_INSTRUMENT not in result.reasons


# --- failure injection: dependency exception -----------------------------------


def test_mandate_revision_hash_mismatch_error_type_is_value_error_subclass():
    """MandateRevisionHashMismatchError must be a ValueError subclass so that
    callers catching ValueError around bind() still pick it up (fail-closed)."""
    from src.core.portfolio.mandate_binding import MandateRevisionHashMismatchError

    assert issubclass(MandateRevisionHashMismatchError, ValueError)


def test_float_quantity_rejected_by_binding_result_validator():
    """BindingResult._no_float validator must reject float quantities — a
    common bug vector when callers pass float instead of Decimal."""
    from pydantic import ValidationError

    from src.core.portfolio.mandate_binding import BindingResult

    with pytest.raises(ValidationError):
        BindingResult(quantity=10.5, reasons=[], denied=False)


# --- D2 성능 단언 (performance assertion) --------------------------------------


@pytest.mark.perf
def test_bind_p99_latency_within_pretrade_gate_budget():
    """ADR-2026-09-09-C Decision 1 축별 성능 예산: 사전거래 게이트 p99 5ms.
    `bind()`가 바로 그 사전거래 사이징 게이트다(모듈 docstring -- 매수/매도
    수량이 거래소로 나가기 직전에 호출됨)."""
    m = mandate(
        max_single_instrument_pct=20.0,
        max_total_exposure_pct=30.0,
        min_cash_buffer_pct=10.0,
    )
    a = agg(
        total_equity=Decimal("10000"),
        per_symbol_pct={"BTC/USDT": Decimal("5")},
        total_exposure_pct=Decimal("10"),
    )
    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        bind(qty=Decimal("50"), price=Decimal("100"), symbol="BTC/USDT", agg=a, mandate=m)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p99_seconds = samples[int(len(samples) * 0.99)]
    assert p99_seconds < 0.005, f"p99={p99_seconds * 1000:.3f}ms exceeds 5ms budget"
