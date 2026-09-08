"""EM-4 domain/route/fee_model.py -- tier selection, boundary bps, negative cases."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.ems.domain.route.fee_model import (
    FeeTier,
    expected_fee_bps,
    fee_bps_for_liquidity,
    select_fee_tier,
)

_TIERS = (
    FeeTier(min_30d_volume=Decimal("0"), maker_bps=Decimal("10"), taker_bps=Decimal("15")),
    FeeTier(
        min_30d_volume=Decimal("1000000"), maker_bps=Decimal("8"), taker_bps=Decimal("12")
    ),
    FeeTier(min_30d_volume=Decimal("5000000"), maker_bps=Decimal("2"), taker_bps=Decimal("6")),
)


# -- tier boundary: exactly at threshold selects the upper tier ------------


def test_volume_just_below_threshold_stays_in_lower_tier() -> None:
    tier = select_fee_tier(_TIERS, Decimal("999999"))
    assert tier.min_30d_volume == Decimal("0")


def test_volume_exactly_at_threshold_selects_upper_tier() -> None:
    tier = select_fee_tier(_TIERS, Decimal("1000000"))
    assert tier.min_30d_volume == Decimal("1000000")


def test_volume_just_above_threshold_stays_in_upper_tier() -> None:
    tier = select_fee_tier(_TIERS, Decimal("1000001"))
    assert tier.min_30d_volume == Decimal("1000000")


def test_boundary_bps_differ_across_the_three_points() -> None:
    below = fee_bps_for_liquidity(select_fee_tier(_TIERS, Decimal("999999")), "TAKER")
    at = fee_bps_for_liquidity(select_fee_tier(_TIERS, Decimal("1000000")), "TAKER")
    above = fee_bps_for_liquidity(select_fee_tier(_TIERS, Decimal("1000001")), "TAKER")
    assert below == Decimal("15")
    assert at == Decimal("12")
    assert above == Decimal("12")


# -- maker/taker split ------------------------------------------------------


def test_expected_fee_bps_reads_maker_vs_taker() -> None:
    assert expected_fee_bps(_TIERS, Decimal("0"), "MAKER") == Decimal("10")
    assert expected_fee_bps(_TIERS, Decimal("0"), "TAKER") == Decimal("15")


def test_highest_tier_applies_for_very_large_volume() -> None:
    tier = select_fee_tier(_TIERS, Decimal("999999999"))
    assert tier.min_30d_volume == Decimal("5000000")


# -- negative: malformed schedule / inputs fail closed ---------------------


def test_empty_tier_schedule_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        select_fee_tier((), Decimal("0"))


def test_negative_trailing_volume_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        select_fee_tier(_TIERS, Decimal("-1"))


def test_unsorted_tier_schedule_is_rejected() -> None:
    unsorted_tiers = (_TIERS[1], _TIERS[0], _TIERS[2])
    with pytest.raises(ValueError, match="sorted ascending"):
        select_fee_tier(unsorted_tiers, Decimal("0"))


def test_duplicate_threshold_is_rejected() -> None:
    duplicate_tiers = (
        FeeTier(min_30d_volume=Decimal("0"), maker_bps=Decimal("10"), taker_bps=Decimal("15")),
        FeeTier(min_30d_volume=Decimal("0"), maker_bps=Decimal("9"), taker_bps=Decimal("14")),
    )
    with pytest.raises(ValueError, match="duplicate"):
        select_fee_tier(duplicate_tiers, Decimal("0"))


def test_schedule_not_starting_at_zero_is_rejected() -> None:
    tiers_without_floor = (
        FeeTier(min_30d_volume=Decimal("100"), maker_bps=Decimal("10"), taker_bps=Decimal("15")),
    )
    with pytest.raises(ValueError, match="min_30d_volume == 0"):
        select_fee_tier(tiers_without_floor, Decimal("0"))


def test_fee_tier_rejects_negative_bps() -> None:
    with pytest.raises(ValueError, match="rebate"):
        FeeTier(min_30d_volume=Decimal("0"), maker_bps=Decimal("-1"), taker_bps=Decimal("5"))
