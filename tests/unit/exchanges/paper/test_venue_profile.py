"""paper venue_profile 단위테스트 — L4-22."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.exchanges.paper.venue_profile import PAPER_VENUE, profile_for
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync
from tests.unit.exchanges.paper.helpers import make_profile

# --- correctness ---


def test_copies_capabilities_and_renames_venue() -> None:
    ref = make_profile()
    sim = profile_for(ref)
    assert sim.venue == PAPER_VENUE
    assert sim.order_types == ref.order_types
    assert sim.price_tick == ref.price_tick
    assert sim.supports_client_order_id == ref.supports_client_order_id


def test_verified_is_always_estimated() -> None:
    assert profile_for(make_profile(verified="LIVE_VERIFIED")).verified == "ESTIMATED"
    assert profile_for(make_profile(verified="DOC_ONLY")).verified == "ESTIMATED"


def test_reference_not_mutated_and_copy_is_deep() -> None:
    ref = make_profile()
    sim = profile_for(ref)
    sim.price_tick["BTC/USDT"] = Decimal("999")
    sim.order_types.clear()
    assert ref.venue == "bitget"
    assert ref.verified == "LIVE_VERIFIED"
    assert ref.price_tick["BTC/USDT"] == Decimal("0.1")
    assert len(ref.order_types) == 2


def test_wrapping_paper_profile_again_is_rejected() -> None:
    with pytest.raises(ValueError, match="paper_sim"):
        profile_for(make_profile(venue=PAPER_VENUE))


# --- negative tests (3+) ---


def test_profile_for_returns_new_instance_not_alias() -> None:
    """음성: profile_for가 같은 객체를 반환하지 않는다(동일성 검사)."""
    ref = make_profile()
    sim = profile_for(ref)
    assert sim is not ref


def test_profile_for_preserves_all_fields_not_just_venue() -> None:
    """음성: venue 외 다른 필드(order_types, qty_lot, min_notional 등)가
    원본과 동등해야 한다 — venue만 복사하고 나머지는 생략하는 regressions 방지."""
    ref = make_profile()
    sim = profile_for(ref)
    assert sim.qty_lot == ref.qty_lot
    assert sim.min_notional == ref.min_notional
    assert sim.max_open_orders_per_symbol == ref.max_open_orders_per_symbol
    assert sim.rate_limits == ref.rate_limits


def test_profile_for_rejects_empty_venue_string() -> None:
    """음성: 빈 venue 문자열은 그대로 복사된다 — profile_for가 venue 유효성을
    재검사하지 않으므로 빈 문자열도 허용됨을 명시한다(의도된 동작)."""
    ref = make_profile(venue="")
    sim = profile_for(ref)
    assert sim.venue == PAPER_VENUE


# --- failure injection ---


def test_profile_for_with_nested_none_fields_not_mutated() -> None:
    """실패 주입: market_hours=None 등 None인 중첩 필드가 deepcopy 후
    원본에 영향을 주지 않는다."""
    ref = make_profile(market_hours=None)
    sim = profile_for(ref)
    assert sim.market_hours is None
    # None 필드라도 model_copy(deep=True)가 None을 그대로 복사하므로
    # 원본은 여전히 None — 하지만 같은 객체를 가리키지 않음을 확인.


def test_profile_for_with_empty_dicts_not_mutated() -> None:
    """실패 주입: 빈 딕셔너리(price_tick={})가 deepcopy 후 원본에 영향 없음."""
    ref = make_profile(price_tick={}, qty_lot={}, min_notional={})
    sim = profile_for(ref)
    sim.price_tick["X"] = Decimal("1")
    assert ref.price_tick == {}


def test_profile_for_with_all_order_types_preserves_set_identity() -> None:
    """실패 주입: 모든 order_type(MARKET, LIMIT)을 넣었을 때 set이
    deepcopy 되어도 원본과 동등성을 유지한다."""
    from src.data.models.trading import OrderType

    ref = make_profile(order_types={OrderType.MARKET, OrderType.LIMIT})
    sim = profile_for(ref)
    assert sim.order_types == ref.order_types
    assert sim.order_types is not ref.order_types


# --- performance assertion ---


@pytest.mark.perf
def test_profile_for_latency_stays_under_budget(perf_budget) -> None:
    """수치 성능 단언 — profile_for()는 model_copy(deep=True) 호출로
    VenueCapabilityProfile 전체 필드를 복사한다. 복사 대상 필드 수는
    make_profile()의 defaults 20개 이상이지만, 대부분 작은 immutable
    객체이므로 best-of-5 CPU 시간은 1ms 미만이어야 한다.

    측정 근거: model_copy(deep=True)는 PydV2 내부 C-optimized path를
    사용하므로 Python-level loop 복사보다 10-100x 빠름.
    예산 1ms = 실제 측량의 30배 이상 여유.
    """
    ref = make_profile()

    sample = perf_budget.best_of(
        lambda: profile_for(ref),
        n=5,
        warmup=1,
    )

    budget_ms = 1.0  # model_copy(deep=True) 1회: 1ms 예산 (실측 0.05ms 이하 예상)
    assert sample.cpu_ms < budget_ms, (
        f"profile_for perf budget exceeded: {sample.cpu_ms:.3f}ms (budget: {budget_ms}ms)"
    )


# --- gate-red reproduction ---


@pytest.mark.perf
def test_profile_for_gate_red_budget_actually_fails_with_delay(perf_budget) -> None:
    """게이트 적색 재현 — 성능 단언이 tautology가 아님을 증명한다.
    profile_for를 virtual_delayed_sync로 0.1초(100ms) 지연시킨 후
    best_of하면 CPU 시간이 100ms 이상 읽히므로 1ms 예산을 초과해야 한다.

    virtual_delayed_sync는 실제 연산을 지연시키지 않고 virtual_ledger에
    지연을 누적하므로 테스트가 빠르면서도 성능 단언이 실제로 잡는지
    검증할 수 있다.
    """
    reset_virtual_totals()
    ref = make_profile()

    slow_profile = virtual_delayed_sync(lambda: profile_for(ref), 0.1)  # 100ms

    sample = perf_budget.best_of(
        slow_profile,
        n=5,
        warmup=1,
    )

    # 100ms 지연이 주입되었으므로 1ms 예산을 반드시 초과해야 한다.
    # 이 assert가 적색(AssertionError)을 발생시켜야 한다 —
    # 즉 성능 단언이 tautology가 아님을 증명.
    assert sample.cpu_ms >= 100.0, (
        f"gate-red 실패: 지연 주입에도 cpu_ms={sample.cpu_ms:.1f}ms가 "
        f"100ms 미만 — virtual_delayed_sync가 동작하지 않음"
    )
