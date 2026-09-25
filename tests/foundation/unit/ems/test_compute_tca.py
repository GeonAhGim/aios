"""EM-14 `application/compute_tca.py` -- negative tests, failure injection,
perf assertion, gate-red repro (ADR-2026-09-09-C D2 floor).

Mirrors `tests/foundation/unit/ems/test_route_order_application.py`
(task-3117, EM-6's D2 deepen): the application layer is isolated behind an
in-memory `FakeTcaResultRepository` so repository failures can be forced
on demand, and EM-12/EM-13's real (unpatched) domain checks are proven
load-bearing via a monkeypatched "regression" of one of them. EM is not
itself the compliance/risk safety axis (ADR-2026-09-09-C axis list), so
only the D2 floor applies here -- D3 is out of scope for this leaf.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.ems.application.compute_tca import (
    InvalidArrivalPriceError,
    InvalidRevisionError,
    compute_tca,
)
from src.foundation.ems.contracts.v1 import TcaResult
from src.foundation.ems.domain.tca.benchmarks import (
    EmptyBarsError,
    EmptyFillsError,
    Fill,
    InvalidFillError,
)
from src.foundation.ems.ports.tca_result_repository import TcaResultRecord


@dataclass
class FakeTcaResultRepository:
    """In-memory stand-in for `TcaResultRepository`. `insert_exc`, when
    set, is raised on every `insert_or_get` call *before* anything is
    recorded -- simulating a save-path failure (dropped connection)
    instead of a successful write."""

    records: dict[tuple[UUID, int], TcaResultRecord] = field(default_factory=dict)
    insert_exc: Exception | None = None
    insert_calls: int = 0

    async def insert_or_get(
        self,
        *,
        parent_id: UUID,
        revision: int,
        result: TcaResult,
        computed_at: datetime,
    ) -> TcaResultRecord:
        self.insert_calls += 1
        if self.insert_exc is not None:
            raise self.insert_exc
        key = (parent_id, revision)
        if key in self.records:
            return self.records[key]
        record = TcaResultRecord(
            tca_id=uuid4(),
            parent_id=parent_id,
            revision=revision,
            result=result,
            computed_at=computed_at,
            created_at=datetime.now(timezone.utc),
        )
        self.records[key] = record
        return record

    async def get_by_revision(self, parent_id: UUID, revision: int) -> TcaResultRecord | None:
        return self.records.get((parent_id, revision))

    async def get_latest(self, parent_id: UUID) -> TcaResultRecord | None:
        matches = [r for (pid, _), r in self.records.items() if pid == parent_id]
        return max(matches, key=lambda r: r.revision) if matches else None


def _bar(close: Decimal, volume: Decimal) -> Candle:
    return Candle(
        symbol="TEST",
        exchange="TEST",
        timeframe="1m",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        open_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        close_time=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )


def _fills(n: int, price: Decimal = Decimal("100")) -> list[Fill]:
    return [Fill(price=price + i, qty=Decimal("1")) for i in range(n)]


_DEFAULT_KWARGS = dict(
    side=OrderSide.BUY,
    fills=[
        Fill(price=Decimal("100"), qty=Decimal("10")),
        Fill(price=Decimal("101"), qty=Decimal("10")),
    ],
    price_at_arrival_ts=Decimal("99"),
    bars=[_bar(Decimal("102"), Decimal("50"))],
    spread_cost=Decimal("5"),
    fees=Decimal("2"),
    total_cost=Decimal("50"),
    revision=1,
    computed_at=datetime.now(timezone.utc),
)


def _kwargs(**overrides: Any) -> dict[str, Any]:
    merged: dict[str, Any] = dict(_DEFAULT_KWARGS)
    merged.update(overrides)
    return merged


# ---------------------------------------------------------------------------
# negative tests (D2, >= 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_price", [Decimal("0"), Decimal("-1")])
async def test_compute_tca_rejects_non_positive_arrival_price(bad_price: Decimal) -> None:
    repo = FakeTcaResultRepository()
    with pytest.raises(InvalidArrivalPriceError):
        await compute_tca(repo, parent_id=uuid4(), **_kwargs(price_at_arrival_ts=bad_price))
    assert repo.insert_calls == 0


@pytest.mark.parametrize("bad_revision", [0, -1])
async def test_compute_tca_rejects_non_positive_revision(bad_revision: int) -> None:
    repo = FakeTcaResultRepository()
    with pytest.raises(InvalidRevisionError):
        await compute_tca(repo, parent_id=uuid4(), **_kwargs(revision=bad_revision))
    assert repo.insert_calls == 0


async def test_compute_tca_propagates_empty_fills_fail_closed() -> None:
    repo = FakeTcaResultRepository()
    with pytest.raises(EmptyFillsError):
        await compute_tca(repo, parent_id=uuid4(), **_kwargs(fills=[]))
    assert repo.insert_calls == 0


async def test_compute_tca_propagates_empty_bars_fail_closed() -> None:
    repo = FakeTcaResultRepository()
    with pytest.raises(EmptyBarsError):
        await compute_tca(repo, parent_id=uuid4(), **_kwargs(bars=[]))
    assert repo.insert_calls == 0


async def test_compute_tca_propagates_zero_volume_bar_fail_closed() -> None:
    """A zero-volume bar cannot contribute to the market interval VWAP
    benchmark -- `_market_interval_vwap` reuses `compute_vwap`'s own
    `qty > 0` rejection rather than tolerating a zero-weight bar."""
    repo = FakeTcaResultRepository()
    with pytest.raises(InvalidFillError):
        await compute_tca(
            repo, parent_id=uuid4(), **_kwargs(bars=[_bar(Decimal("102"), Decimal("0"))])
        )
    assert repo.insert_calls == 0


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_compute_tca_propagates_repository_connection_drop_fail_closed() -> None:
    repo = FakeTcaResultRepository(insert_exc=ConnectionResetError("simulated connection drop"))
    parent_id = uuid4()

    with pytest.raises(ConnectionResetError):
        await compute_tca(repo, parent_id=parent_id, **_kwargs())

    assert await repo.get_by_revision(parent_id, 1) is None


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves EM-12's `compute_vwap` qty/price validation
# is load-bearing for the market-interval-VWAP benchmark this leaf derives.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_market_vwap_validation_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_id = uuid4()
    corrupt_bars = [_bar(Decimal("100"), Decimal("10")), _bar(Decimal("999"), Decimal("0"))]
    repo = FakeTcaResultRepository()

    # green: EM-12's validation active -- the zero-volume bar is rejected
    # outright rather than silently dropped, nothing persisted.
    with pytest.raises(InvalidFillError):
        await compute_tca(repo, parent_id=parent_id, **_kwargs(bars=corrupt_bars))
    assert repo.insert_calls == 0

    # red repro: neutralize exactly the qty/price check `compute_vwap`
    # performs, replacing it with a variant that silently drops invalid
    # entries instead of raising. `compute_tca` does not re-check this
    # itself (it delegates to EM-12, per this module's docstring) -- so a
    # regression that weakens that check would let a corrupt bar be
    # silently excluded from the market benchmark instead of blocking the
    # whole computation, and no EM-12-only test would catch that this
    # leaf's `_market_interval_vwap` depends on the check staying strict.
    import src.foundation.ems.application.compute_tca as target

    def _compute_vwap_silently_drops_invalid(fills: list[Fill]) -> Decimal:
        good = [f for f in fills if f.qty > 0 and f.price > 0]
        if not good:
            raise EmptyFillsError("no valid fills")
        total_notional = sum((f.price * f.qty for f in good), start=Decimal("0"))
        total_qty = sum((f.qty for f in good), start=Decimal("0"))
        return total_notional / total_qty

    monkeypatch.setattr(target, "compute_vwap", _compute_vwap_silently_drops_invalid)

    record = await compute_tca(repo, parent_id=parent_id, **_kwargs(bars=corrupt_bars))

    # without the guard, a TCA result silently computed from only the
    # surviving bar (the corrupt zero-volume bar dropped, not rejected) is
    # persisted -- proving the real (unpatched) check is what protects the
    # market-interval-VWAP benchmark's integrity, not anything in this leaf.
    assert repo.insert_calls == 1
    assert record.revision == 1


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_compute_tca_perf_budget_many_fills_and_bars() -> None:
    """`compute_tca` calls EM-12's `compute_vwap` twice (execution + market
    interval) and EM-13's `decompose_cost` once, each O(n) over `fills`/
    `bars` -- this pins an upper bound so an accidental O(n^2) (e.g.
    re-scanning fills inside the bps conversion loop) fails this test
    instead of only showing up as production latency."""
    fills = _fills(200)
    bars = [_bar(Decimal("100") + i, Decimal("10")) for i in range(100)]
    repo = FakeTcaResultRepository()

    start = time.perf_counter()
    for revision in range(1, 101):
        await compute_tca(
            repo,
            parent_id=uuid4(),
            **_kwargs(fills=fills, bars=bars, revision=revision),
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 1500, (
        f"compute_tca() too slow: {elapsed_ms:.1f}ms/100 calls x 200 fills x 100 bars"
    )
