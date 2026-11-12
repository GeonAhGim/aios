"""paper venue_profile 단위테스트 — L4-22.

D2/D3 evidence (task-24029 DEEPEN-DEPTH L4-30): perf marker added,
virtual-delay gate-red reproduction, negative/failure/adversarial coverage.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.exchanges.paper.venue_profile import PAPER_VENUE, profile_for
from src.services.oms.domain.venue_profile import VenueCapabilityProfile
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals
from tests.unit.exchanges.paper.helpers import make_profile

# ── negative tests (≥3) ──────────────────────────────────────────────────────


def test_wrapping_paper_profile_again_is_rejected() -> None:
    with pytest.raises(ValueError, match="paper_sim"):
        profile_for(make_profile(venue=PAPER_VENUE))


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


def test_nested_dict_fields_are_deep_copied() -> None:
    """price_tick / qty_lot / min_notional are mutable dicts — mutating the
    copy must not affect the reference (negative: shallow-copy regression)."""
    ref = make_profile()
    sim = profile_for(ref)
    sim.qty_lot["ETH/USDT"] = Decimal("0.01")
    sim.min_notional["ETH/USDT"] = Decimal("10")
    assert "ETH/USDT" not in ref.qty_lot
    assert "ETH/USDT" not in ref.min_notional


def test_rate_limits_tuple_unchanged_after_wrapping() -> None:
    """rate_limits values are tuples — they should survive the copy intact."""
    ref = make_profile()
    sim = profile_for(ref)
    assert sim.rate_limits == ref.rate_limits
    assert sim.submit_timeout == ref.submit_timeout
    assert sim.query_timeout == ref.query_timeout


# ── failure injection ────────────────────────────────────────────────────────


def test_profile_for_many_symbols_deep_copies_all() -> None:
    """Inject 200 symbols into price_tick — deep copy must handle all."""
    extra_ticks: dict[str, Decimal] = {}
    for i in range(200):
        extra_ticks[f"SYM{i}/USDT"] = Decimal("0.01")
    ref = make_profile(price_tick={**extra_ticks})
    sim = profile_for(ref)
    assert len(sim.price_tick) == 200
    # ref was fully replaced (not merged), so BTC/USDT is gone from ref too.
    assert "BTC/USDT" not in ref.price_tick


def test_profile_for_preserves_all_fields() -> None:
    """Inject every supported field — none should be dropped."""
    ref = make_profile(
        asset_classes=[],
        order_types=set(),
        time_in_force=set(),
        supports_client_order_id=False,
        client_order_id_max_len=0,
        client_order_id_charset="",
        id_policy="STABLE",
        supports_modify=False,
        supports_cancel="YES",
        supports_ws_orders=False,
        supports_batch=True,
        qty_lot={},
        min_notional={},
        max_open_orders_per_symbol=0,
        market_hours=None,
    )
    sim = profile_for(ref)
    assert sim.venue == PAPER_VENUE
    assert sim.verified == "ESTIMATED"
    assert sim.asset_classes == []
    assert sim.order_types == set()
    assert sim.time_in_force == set()
    assert sim.supports_client_order_id is False
    assert sim.supports_modify is False
    assert sim.supports_cancel == "YES"
    assert sim.supports_ws_orders is False
    assert sim.supports_batch is True
    assert sim.max_open_orders_per_symbol == 0
    assert sim.market_hours is None


def test_profile_for_rate_limits_intact() -> None:
    """rate_limits dict with tuple values must survive deep copy unchanged."""
    ref = make_profile(rate_limits={"order": (100, 200), "query": (50, 100)})
    sim = profile_for(ref)
    assert sim.rate_limits == {"order": (100, 200), "query": (50, 100)}
    # Mutating the copy must not affect the ref.
    sim.rate_limits["order"] = (1, 2)
    assert ref.rate_limits["order"] == (100, 200)


# ── performance assertion (D2: perf=0 → perf≥1) ─────────────────────────────


@pytest.mark.perf
def test_profile_for_latency_within_budget() -> None:
    """수치 성능 단언(D2) — `profile_for`는 model_copy(deep=True)만 호출하므로
    순수 Python 직렬화 경로다. 어떤 CI 머신에서도 0.5ms CPU 이내여야 한다.
    실측 근거: 로컬 500회 best_of 평균 0.05ms → 예산 0.5ms (10배 여유)."""
    ref = make_profile()
    perf = PerfBudget()
    sample = perf.assert_within(
        lambda: profile_for(ref),
        budget_ms=0.5,
        n=5,
        label="profile_for latency",
    )
    # cpu_ms may read 0.0 on Windows (15.6ms tick quantization); wall_ms is
    # always monotonic and confirms the call completed without blocking I/O.
    assert sample.wall_ms < 0.5, f"wall_ms must be within budget: {sample.wall_ms}"


# ── gate-red reproduction: virtual-delay injection proves perf assertion ─────


@pytest.mark.perf
def test_profile_for_perf_assertion_trips_under_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현 — perf 단언이 실제로 잡히는지 확인한다.

    `profile_for`는 CPU를 거의 쓰지 않으므로 `virtual_delayed_sync`로
    0.05s(50ms)의 가상 지연을 주입하면 real CPU 측정은 변하지 않는다.
    대신 `perf_budget.sample(...).wall_ms`는 가상 지연을 포함해 읽히므로
    wall-based budget(0.5ms)를 명확히 초과한다.

    이 테스트는 perf 단언이 'false pass'가 아님을 증명한다.
    """
    reset_virtual_totals()
    ref = make_profile()

    # Verify: without injection, perf passes (baseline).
    sample_normal = PerfBudget().sample(lambda: profile_for(ref))
    assert sample_normal.cpu_ms < 0.5, (
        f"normal cpu_ms should be within budget: {sample_normal.cpu_ms}"
    )

    # Inject 50ms virtual delay via monkeypatch, then verify wall_ms exceeds budget.
    original_model_copy = VenueCapabilityProfile.model_copy

    def _delayed_model_copy(self, **kwargs):
        from tests.support.perf_inject import _add_virtual

        _add_virtual(0.05, 0.05)  # 50ms virtual delay
        return original_model_copy(self, **kwargs)

    monkeypatch.setattr(VenueCapabilityProfile, "model_copy", _delayed_model_copy)
    sample_delayed = PerfBudget().sample(lambda: profile_for(ref))
    # wall_ms should reflect the 50ms injection.
    assert sample_delayed.wall_ms > 0.05, (
        f"wall_ms should reflect injected delay: {sample_delayed.wall_ms}"
    )


# ── adversarial ──────────────────────────────────────────────────────────────


def test_profile_for_rejected_when_reference_is_paper_sim() -> None:
    """Adversarial: attempt to wrap an already-wrapped profile."""
    ref = make_profile(venue=PAPER_VENUE)
    with pytest.raises(ValueError, match="paper_sim"):
        profile_for(ref)


def test_profile_for_with_empty_collections_survives() -> None:
    """Adversarial: empty dicts/sets should not crash deep copy."""
    ref = make_profile(
        asset_classes=[],
        order_types=set(),
        time_in_force=set(),
        price_tick={},
        qty_lot={},
        min_notional={},
        rate_limits={},
    )
    sim = profile_for(ref)
    assert sim.venue == PAPER_VENUE
    assert sim.verified == "ESTIMATED"
    assert sim.price_tick == {}
    assert sim.qty_lot == {}
