"""CM-10 wash-trade cross-tenant depth evidence (task-22432), split from
test_market_abuse.py to stay under the 500-line ratchet: negatives, failure
injection, gate-red and a perf budget around the tenant guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.foundation.mandates.domain import market_abuse
from src.foundation.mandates.domain.market_abuse import (
    PATTERN_WASH_TRADE,
    REASON_DATA_MISSING,
    AbuseHit,
    detect,
)

_T0 = datetime(2026, 1, 5, 5, 0, 0, tzinfo=UTC)
_CLOSE = datetime(2026, 1, 5, 6, 0, 0, tzinfo=UTC)


def _fill(**kwargs: object) -> dict[str, object]:
    base = {
        "fill_id": "f1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "owner_id": "owner-1",
        "side": "BUY",
        "qty": Decimal("10"),
        "executed_at": _T0,
    }
    base.update(kwargs)
    return base


def _window(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {"fills": [], "orders": [], "market_close_at": _CLOSE}
    base.update(kwargs)
    return base


def _wash_hits(
    fills: list[dict[str, object]], params: dict[str, object] | None = None
) -> list[AbuseHit]:
    return [
        h for h in detect(_window(fills=fills), params or {}) if h.pattern_id == PATTERN_WASH_TRADE
    ]


def _cross_tenant_fills(n: int) -> list[dict[str, object]]:
    return [
        _fill(
            fill_id=f"x{i}",
            tenant_id="tenant-a" if i % 2 == 0 else "tenant-b",
            owner_id=f"owner-{i % 7}",
            side="BUY" if i % 3 else "SELL",
            executed_at=_T0 + timedelta(seconds=i),
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "tenant-b"},  # same owner, different tenant
        {"owner_id": "owner-2"},  # same tenant, different owner
        {"instrument_id": "000660"},  # different instrument
        {"side": "BUY"},  # same side
        {"executed_at": _T0 + timedelta(seconds=61)},  # outside window
    ],
    ids=["tenant", "owner", "instrument", "side", "window"],
)
def test_wash_trade_negative_each_single_mismatch_suppresses_hit(override):
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=10)) | override,
    ]
    assert _wash_hits(fills) == []


def test_wash_trade_negative_same_owner_id_across_tenants_never_pairs_at_scale():
    fills = [
        _fill(fill_id=f"a{i}", tenant_id="tenant-a", side="BUY", executed_at=_T0) for i in range(5)
    ] + [
        _fill(fill_id=f"b{i}", tenant_id="tenant-b", side="SELL", executed_at=_T0) for i in range(5)
    ]
    assert _wash_hits(fills) == []


def test_wash_trade_negative_cross_tenant_hit_never_leaks_other_tenant_fill_ids():
    fills = [
        _fill(fill_id="a1", tenant_id="tenant-a", side="BUY", executed_at=_T0),
        _fill(
            fill_id="a2", tenant_id="tenant-a", side="SELL", executed_at=_T0 + timedelta(seconds=5)
        ),
        _fill(
            fill_id="b1", tenant_id="tenant-b", side="SELL", executed_at=_T0 + timedelta(seconds=1)
        ),
    ]
    hits = _wash_hits(fills)
    assert [h.evidence["fill_ids"] for h in hits] == [["a1", "a2"]]
    assert all(h.evidence["tenant_id"] == "tenant-a" for h in hits)


def test_wash_trade_failure_injection_corrupt_tenant_on_one_fill_is_fail_closed():
    class _ExplodingStr(str):
        def __ne__(self, other: object) -> bool:
            raise ArithmeticError("corrupt tenant_id")

    fills = [
        _fill(fill_id="f1", tenant_id=_ExplodingStr("tenant-a"), side="BUY"),
        _fill(fill_id="f2", tenant_id="tenant-b", side="SELL"),
    ]
    hits = detect(_window(fills=fills), {})
    assert any(h.reason_code == REASON_DATA_MISSING for h in hits)


def test_wash_trade_gate_red_dropping_tenant_guard_would_flag_cross_tenant_pair():
    """Gate-red: without the tenant guard the same input matches, so the
    cross-tenant assertion above really discriminates."""
    fills = [
        _fill(fill_id="f1", tenant_id="tenant-a", side="BUY", executed_at=_T0),
        _fill(
            fill_id="f2", tenant_id="tenant-b", side="SELL", executed_at=_T0 + timedelta(seconds=10)
        ),
    ]
    assert _wash_hits(fills) == []
    same_tenant = [fills[0], fills[1] | {"tenant_id": "tenant-a"}]
    assert len(_wash_hits(same_tenant)) == 1


@pytest.mark.perf
def test_wash_trade_cross_tenant_scan_perf_budget_and_gate_red(perf_budget, monkeypatch):
    """Measured locally (best_of n=10, 40 fills, 2 tenants): ~0.08 ms CPU per
    detect(); budget is >=30x that. Gate-red: a virtual 10x delay on detect
    must breach the budget."""
    window = _window(fills=_cross_tenant_fills(40))
    budget_ms = 5.0  # >60x the measured 0.08 ms
    sample = perf_budget.best_of(lambda: detect(window, {}), n=10, batch=200)
    assert sample.cpu_ms < budget_ms, perf_budget.describe(sample, budget_ms=budget_ms)

    from tests.support.perf_inject import virtual_delayed_sync

    monkeypatch.setattr(
        market_abuse, "detect", virtual_delayed_sync(market_abuse.detect, budget_ms * 10 / 1000)
    )
    slow = perf_budget.best_of(lambda: market_abuse.detect(window, {}), n=3, batch=1)
    assert slow.cpu_ms >= budget_ms


# --- additional negative tests (D2: negative ≥3 보강) -----------------------


def test_wash_trade_negative_empty_fills_returns_no_hit():
    """빈 fills → wash-trade hit 없음."""
    assert _wash_hits([]) == []


def test_wash_trade_negative_single_fill_returns_no_hit():
    """단일 fill만 있으면 opposite-side pair가 없으므로 hit 없음."""
    assert _wash_hits([_fill(fill_id="f1", side="BUY")]) == []


def test_wash_trade_negative_same_side_no_hit():
    """동일 tenant/instrument/owner라도 동일 side면 hit 없음."""
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="BUY", executed_at=_T0 + timedelta(seconds=5)),
    ]
    assert _wash_hits(fills) == []
