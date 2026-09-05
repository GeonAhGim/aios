"""paper venue_profile 단위테스트 — L4-22."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.exchanges.paper.venue_profile import PAPER_VENUE, profile_for
from tests.unit.exchanges.paper.helpers import make_profile


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
