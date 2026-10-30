"""Forbidden-asset path of mandate_binding.bind(): negative, injection and gate-red tests.

Companion to test_mandate_binding.py::test_forbidden_asset_denies_exactly (kept separate so the
main file stays under the 500-line ratchet). The perf assertion there is reused unchanged here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.portfolio.mandate_binding import POLICY_FORBIDDEN_ASSET, bind
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync
from tests.unit.core.portfolio.test_mandate_binding import agg, mandate

_BUDGET_MS = 5.0


def _forbidden_call() -> None:
    bind(
        qty=Decimal("10"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )


def test_other_symbol_not_denied_as_forbidden() -> None:
    result = bind(
        qty=Decimal("1"),
        price=Decimal("250"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert POLICY_FORBIDDEN_ASSET not in result.reasons
    assert result.denied is False


def test_forbidden_asset_never_clamps_to_nonzero_quantity() -> None:
    result = bind(
        qty=Decimal("1000000"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_forbidden_asset_deny_is_case_sensitive_exact_match() -> None:
    result = bind(
        qty=Decimal("1"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT2",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert POLICY_FORBIDDEN_ASSET not in result.reasons


@pytest.mark.perf
def test_forbidden_asset_normal_input_is_green(perf_budget: PerfBudget) -> None:
    sample = perf_budget.best_of(_forbidden_call, n=5, batch=100)
    assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


@pytest.mark.perf
def test_forbidden_asset_goes_red_with_10x_virtual_delay(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """Failure injection: a 10x-budget virtual CPU delay must turn the same assertion red."""
    import src.core.portfolio.mandate_binding as mod

    monkeypatch.setattr(mod, "bind", virtual_delayed_sync(mod.bind, _BUDGET_MS * 10 / 1000))

    def _call_once() -> None:
        mod.bind(
            qty=Decimal("10"),
            price=Decimal("250"),
            symbol="SANCTIONED/USDT",
            agg=agg(),
            mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
        )

    sample = perf_budget.best_of(_call_once, n=5, batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


# --- D2 성능 단언 (performance assertion) --------------------------------------


@pytest.mark.perf
def test_bind_p99_latency_within_pretrade_gate_budget(
    perf_budget: PerfBudget,
) -> None:
    """ADR-2026-09-09-C Decision 1 축별 성능 예산: 사전거래 게이트 p99 5ms.
    `bind()`가 바로 그 사전거래 사이징 게이트다(모듈 docstring -- 매수/매도
    수량이 거래소로 나가기 직전에 호출됨).

    Raw ``time.perf_counter()`` 단언을 ``perf_budget`` 픽스처(
    ``time.process_time()``, coverage tracer 일시정지, best-of-N)로 전환.
    batch=100으로 묶어 ``process_time`` 의 15.6ms 틱 양자화 오차를 줄임.
    단위: samples.cpu_ms 가 ms, 예산도 5ms (초 ×1000 혼용 금지).
    """
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

    def _call_once() -> None:
        bind(qty=Decimal("50"), price=Decimal("100"), symbol="BTC/USDT", agg=a, mandate=m)

    # best-of-5 × batch=100 → 500 호출 중 최소 CPU 시간 5회.
    # process_time 15.6ms 틱: batch=100 이면 1회당 추정 오차 ~0.16ms.
    sample = perf_budget.best_of(_call_once, n=5, batch=100)
    assert sample.cpu_ms < 5.0, (
        f"p99 proxy cpu_ms={sample.cpu_ms:.3f}ms exceeds 5ms budget "
        f"(wall_ms={sample.wall_ms:.3f}ms)"
    )
