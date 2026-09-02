"""TWAP/VWAP/POV/iceberg 슬라이스 계획기 단위테스트 — L4-06. DB 없음."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.services.oms.contracts.v1_commands import AlgoRequest, IdempotencyScope
from src.services.oms.domain.algo_slicer import plan_slices


def _scope() -> IdempotencyScope:
    return IdempotencyScope(
        tenant_id=uuid4(),
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )


def _request(**overrides: object) -> AlgoRequest:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    defaults: dict[str, object] = {
        "algo_run_id": uuid4(),
        "trace_id": uuid4(),
        "scope": _scope(),
        "algo": "TWAP",
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "total_quantity": Decimal("1"),
        "start_at": start,
        "end_at": start + timedelta(minutes=10),
        "slice_count": 5,
        "seed": 42,
    }
    defaults.update(overrides)
    return AlgoRequest(**defaults)  # type: ignore[arg-type]


def test_slices_sum_exactly_to_total_quantity() -> None:
    req = _request(total_quantity=Decimal("1"), slice_count=7)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert sum(p.quantity for p in plans) == req.total_quantity


def test_slice_count_matches_request() -> None:
    req = _request(slice_count=5)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert len(plans) == 5


def test_same_seed_produces_identical_plan() -> None:
    req = _request()
    plans_a = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    plans_b = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert plans_a == plans_b


def test_different_seed_produces_different_plan() -> None:
    req = _request()
    plans_a = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(1))
    plans_b = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(2))
    assert plans_a != plans_b


def test_all_scheduled_times_within_window() -> None:
    req = _request()
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    for plan in plans:
        assert req.start_at <= plan.scheduled_at <= req.end_at


def test_all_quantities_non_negative() -> None:
    req = _request(size_jitter_pct=Decimal("90"))
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert all(p.quantity >= 0 for p in plans)


def test_participation_cap_limits_non_final_slices() -> None:
    """참여율 상한(anti-front-running) — 슬라이스 qty는 그 구간 예상
    거래량 × max_participation_pct를 넘지 않는다(마지막 슬라이스는 잔량
    흡수라 예외)."""
    req = _request(
        total_quantity=Decimal("100"), slice_count=4, max_participation_pct=Decimal("10")
    )
    volume_profile = [Decimal("50"), Decimal("50"), Decimal("50"), Decimal("50")]
    plans = plan_slices(
        req, now=req.start_at, volume_profile=volume_profile, rng=random.Random(req.seed)
    )
    for plan in plans[:-1]:
        assert plan.quantity <= Decimal("5")  # 50 * 10%


def test_last_slice_absorbs_remainder_even_under_participation_cap() -> None:
    """참여율 상한 때문에 앞선 슬라이스들이 원래 몫보다 적게 나가도,
    마지막 슬라이스가 남은 전량을 흡수해 합계는 정확히 total과 같다."""
    req = _request(
        total_quantity=Decimal("100"), slice_count=4, max_participation_pct=Decimal("1")
    )
    volume_profile = [Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10")]
    plans = plan_slices(
        req, now=req.start_at, volume_profile=volume_profile, rng=random.Random(req.seed)
    )
    assert sum(p.quantity for p in plans) == req.total_quantity
    assert plans[-1].quantity > plans[0].quantity


def test_invalid_time_window_raises() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    req = _request(start_at=start, end_at=start)
    with pytest.raises(ValueError, match="end_at"):
        plan_slices(req, now=start, volume_profile=None, rng=random.Random(req.seed))
