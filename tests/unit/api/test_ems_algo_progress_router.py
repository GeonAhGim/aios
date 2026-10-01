"""EM-15b (task-4033) -- unit tests for `GET /v1/foundation/ems/algo/{parent_id}
/progress` (`src/api/routers/foundation/ems.py::get_algo_run_progress`).

No FastAPI TestClient/DB -- the handler is thin (auth/DI/read-invocation
only, 71 §6), so calling it directly with a fake `AlgoScheduler`-shaped
object and a fake `User` exercises the same code path without I/O. Mirrors
`tests/unit/api/test_compliance_router.py`'s convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.api.routers.foundation.ems import get_algo_run_progress
from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.application.get_algo_progress import (
    AlgoRunNotFoundError,
    get_algo_progress,
)
from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    ParentOrder,
    ParentOrderConstraints,
)
from src.services.auth_service import User

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _user() -> User:
    user_id = uuid4()
    return User(
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, Any] = {
        "kind": AlgoKind.TWAP,
        "start": _T0,
        "end": _T0 + timedelta(minutes=10),
        "max_participation_pct": Decimal("10"),
        "slice_interval_sec": 60,
        "urgency": Decimal("0.5"),
        "seed": 42,
    }
    defaults.update(overrides)
    return AlgoSpec(**defaults)


def _parent(parent_id: UUID, **overrides: object) -> ParentOrder:
    defaults: dict[str, Any] = {
        "parent_id": parent_id,
        "instrument_id": "BTC/USDT",
        "side": OrderSide.BUY,
        "qty": Decimal("10"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.ACKNOWLEDGED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)


def _child(parent_id: UUID, slice_seq: int, qty: Decimal) -> ChildOrder:
    return ChildOrder(
        child_id=uuid4(),
        parent_id=parent_id,
        slice_seq=slice_seq,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        planned_qty=qty,
        scheduled_at=_T0,
    )


@dataclass
class _FakeAlgoScheduler:
    """`get_algo_run_progress` only calls `active_plan` -- mirrors the real
    `AlgoScheduler` shape without needing a pool/orders_repo."""

    plans: dict[UUID, AlgoRunPlan] = field(default_factory=dict)

    def active_plan(self, parent_order_id: UUID) -> AlgoRunPlan | None:
        return self.plans.get(parent_order_id)


async def test_get_algo_run_progress_returns_progress_for_a_registered_run() -> None:
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"))],
    )
    scheduler = _FakeAlgoScheduler(plans={parent_id: plan})

    response = await get_algo_run_progress(parent_id, _user=_user(), scheduler=scheduler)

    assert response.data.parent_id == parent_id
    assert response.data.status == "running"
    assert response.data.total_slices == 1
    assert response.data.remaining_qty == Decimal("10")


async def test_get_algo_run_progress_404s_for_an_unregistered_parent_id() -> None:
    """Negative -- unknown parent_id (never started, already finished, or
    process restarted since it ran) must 404, not 500 or an empty 200."""
    scheduler = _FakeAlgoScheduler()

    with pytest.raises(AlgoRunNotFoundError):
        await get_algo_run_progress(uuid4(), _user=_user(), scheduler=scheduler)


async def test_get_algo_run_progress_404s_for_a_different_registered_parent_id() -> None:
    """Negative -- a plan registered under a different parent_id must not
    leak into this parent_id's progress read."""
    registered_id = uuid4()
    requested_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(registered_id, qty=Decimal("10")),
        children=[_child(registered_id, 0, Decimal("10"))],
    )
    scheduler = _FakeAlgoScheduler(plans={registered_id: plan})

    with pytest.raises(AlgoRunNotFoundError):
        await get_algo_run_progress(requested_id, _user=_user(), scheduler=scheduler)


def test_get_algo_progress_rejects_none_plan_with_the_requested_parent_id() -> None:
    """Negative -- `get_algo_progress` itself must reject a `None` plan (the
    `AlgoScheduler.active_plan` miss case) rather than let a `None` leak into
    the progress computation, and the raised error must identify which
    `parent_id` was requested so a 404 response body/log stays debuggable."""
    parent_id = uuid4()

    with pytest.raises(AlgoRunNotFoundError, match=str(parent_id)):
        get_algo_progress(None, parent_id=parent_id)


async def test_get_algo_run_progress_propagates_scheduler_failures_fail_closed() -> None:
    """Failure injection -- if `AlgoScheduler.active_plan` blows up (e.g. a
    lock/connection error in a future non-in-memory implementation), the
    router must let the exception propagate (-> 500) instead of swallowing it
    into a false 404 or an empty 200, per the fail-closed default (CLAUDE.md
    §3)."""

    @dataclass
    class _ExplodingScheduler:
        def active_plan(self, parent_order_id: UUID) -> AlgoRunPlan | None:
            raise RuntimeError("simulated scheduler lookup failure")

    with pytest.raises(RuntimeError, match="simulated scheduler lookup failure"):
        await get_algo_run_progress(uuid4(), _user=_user(), scheduler=_ExplodingScheduler())


async def test_get_algo_run_progress_completes_within_latency_budget() -> None:
    """Perf -- the handler is a pure in-memory read (no I/O per the module
    docstring), so a single call must complete well under a generous 50ms
    budget; a regression here would indicate an accidental I/O call creeping
    into this read path."""
    import time

    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"))],
    )
    scheduler = _FakeAlgoScheduler(plans={parent_id: plan})

    start = time.perf_counter()
    await get_algo_run_progress(parent_id, _user=_user(), scheduler=scheduler)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50
