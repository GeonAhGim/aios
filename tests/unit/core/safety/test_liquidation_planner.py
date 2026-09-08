"""R-50 — plan_liquidation unit tests (§9 R-50 DoD).

Falsifiable DoD checked here: (a) determinism — identical inputs hash to
the same canonical JSON, a 1-byte seed change changes the jitter; (b) sum
preservation — slice quantities sum to the exact original Decimal, no
float drift; (c) participation cap — no slice exceeds
`max_participation_pct% x volume_5m`; (d) minimum split — a single
position still gets >=`slice_count_min` slices, and an unknown 5m volume
is treated fail-closed (max slice count), never as unconstrained.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from src.core.loader.risk_policy_loader import LiquidationPolicy
from src.core.safety.liquidation_planner import (
    LiquidationPlan,
    OpenPosition,
    plan_liquidation,
)

_BASELINE_POLICY_KWARGS = dict(
    max_participation_pct=10.0,
    slice_count_min=3,
    slice_count_max=20,
    size_jitter_pct=30.0,
    interval_min_sec=2,
    interval_max_sec=15,
    max_slice_notional=5000,
    limit_tolerance_bps=15,
    slice_ttl_sec=5,
    adverse_move_abort_pct=1.0,
    total_deadline_sec=300,
)


def _policy(**overrides: object) -> LiquidationPolicy:
    return LiquidationPolicy(**{**_BASELINE_POLICY_KWARGS, **overrides})


def _position(
    symbol: str = "BTCUSDT",
    quantity: Decimal = Decimal("10.5"),
    notional: Decimal = Decimal("1000"),
    lot_size: Decimal = Decimal("0.001"),
) -> OpenPosition:
    return OpenPosition(symbol=symbol, quantity=quantity, notional=notional, lot_size=lot_size)


def _canonical_hash(plan: LiquidationPlan) -> str:
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()


def test_same_inputs_produce_identical_canonical_hash() -> None:
    position = _position()
    policy = _policy()
    seed = b"\x00" * 32
    volume_5m = {"BTCUSDT": None}

    plan1 = plan_liquidation([position], seed=seed, policy=policy, volume_5m=volume_5m)
    plan2 = plan_liquidation([position], seed=seed, policy=policy, volume_5m=volume_5m)

    assert _canonical_hash(plan1) == _canonical_hash(plan2)


def test_one_byte_seed_change_changes_jitter_and_hash() -> None:
    position = _position()
    policy = _policy()
    volume_5m = {"BTCUSDT": None}
    seed_a = b"\x00" * 32
    seed_b = b"\x01" + seed_a[1:]

    plan_a = plan_liquidation([position], seed=seed_a, policy=policy, volume_5m=volume_5m)
    plan_b = plan_liquidation([position], seed=seed_b, policy=policy, volume_5m=volume_5m)

    assert _canonical_hash(plan_a) != _canonical_hash(plan_b)
    assert [s.quantity for s in plan_a.slices] != [s.quantity for s in plan_b.slices]


def test_slice_quantities_sum_exactly_to_original_decimal() -> None:
    position = _position(quantity=Decimal("10.5"), lot_size=Decimal("0.001"))
    policy = _policy()
    plan = plan_liquidation(
        [position], seed=b"seed", policy=policy, volume_5m={"BTCUSDT": None}
    )

    total = sum((s.quantity for s in plan.slices), Decimal(0))

    assert total == Decimal("10.5")


def test_no_slice_exceeds_participation_cap() -> None:
    # Negligible jitter isolates the cap-enforcement path from jitter noise.
    policy = _policy(size_jitter_pct=0.01, max_participation_pct=10.0)
    position = _position(
        symbol="ETHUSDT", quantity=Decimal("45"), notional=Decimal("100"), lot_size=Decimal("0.01")
    )
    plan = plan_liquidation(
        [position], seed=b"cap-seed", policy=policy, volume_5m={"ETHUSDT": Decimal("100")}
    )

    cap = Decimal("10")
    assert all(s.quantity <= cap for s in plan.slices)
    assert sum((s.quantity for s in plan.slices), Decimal(0)) == Decimal("45")


def test_single_position_gets_at_least_slice_count_min_slices() -> None:
    policy = _policy(slice_count_min=3, slice_count_max=20)
    position = _position(
        symbol="BTCUSDT", quantity=Decimal("1"), notional=Decimal("10"), lot_size=Decimal("0.001")
    )
    plan = plan_liquidation(
        [position], seed=b"min-seed", policy=policy, volume_5m={"BTCUSDT": Decimal("1000000")}
    )

    assert len(plan.slices) == 3
    assert [s.order_type for s in plan.slices] == ["LIMIT", "LIMIT", "MARKET"]


def test_unknown_volume_is_fail_closed_not_unlimited() -> None:
    """volume_5m[symbol] is None must not be read as 'no participation
    limit' — it must fall back to the smallest (max-count) slices instead
    of a single large, unconstrained slice."""
    policy = _policy(slice_count_min=3, slice_count_max=20)
    position = _position(
        symbol="BTCUSDT", quantity=Decimal("5"), notional=Decimal("10"), lot_size=Decimal("0.001")
    )

    plan = plan_liquidation(
        [position], seed=b"unknown-vol", policy=policy, volume_5m={"BTCUSDT": None}
    )

    assert len(plan.slices) == policy.slice_count_max
    assert sum((s.quantity for s in plan.slices), Decimal(0)) == Decimal("5")


def test_plan_metadata_fields() -> None:
    policy = _policy()
    position = _position()
    plan = plan_liquidation(
        [position], seed=b"meta-seed", policy=policy, volume_5m={"BTCUSDT": None}
    )

    assert plan.deadline_offset_sec == int(policy.total_deadline_sec)
    assert plan.adverse_move_abort_pct == Decimal(str(policy.adverse_move_abort_pct))
    assert plan.seed_hash == hashlib.sha256(b"meta-seed").hexdigest()
    assert [s.seq for s in plan.slices] == list(range(len(plan.slices)))
    offsets = [s.not_before_offset_sec for s in plan.slices]
    assert offsets == sorted(offsets)


def test_multiple_positions_produce_contiguous_sequence_numbers() -> None:
    policy = _policy(slice_count_min=3, slice_count_max=5)
    positions = [
        _position(symbol="BTCUSDT", quantity=Decimal("3"), notional=Decimal("10")),
        _position(symbol="ETHUSDT", quantity=Decimal("4"), notional=Decimal("10")),
    ]
    plan = plan_liquidation(
        positions,
        seed=b"multi-seed",
        policy=policy,
        volume_5m={"BTCUSDT": None, "ETHUSDT": None},
    )

    assert [s.seq for s in plan.slices] == list(range(len(plan.slices)))
    symbols_in_order = [s.symbol for s in plan.slices]
    assert symbols_in_order == ["BTCUSDT"] * 5 + ["ETHUSDT"] * 5
