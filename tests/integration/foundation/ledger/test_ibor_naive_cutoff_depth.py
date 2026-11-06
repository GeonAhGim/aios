"""FA-12 `ibor_view.compute_ibor_view` naive-cutoff depth evidence (D2/D3).

Sibling of `test_ibor_abor.py` (kept under the 500-line ratchet, CLAUDE.md §6 #12).
Invariants cross-checked: I-07 (tz-aware UTC instants) / I-10 (fail-closed).
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ledger.application import ibor_view
from src.foundation.ledger.application.ibor_view import NaiveCutoffError


@pytest.mark.perf
@pytest.mark.parametrize(
    "cutoff",
    [datetime(2026, 1, 1), datetime.min, datetime.max.replace(fold=1)],
    ids=["negative_naive", "negative_min", "negative_max_fold"],
)
async def test_ibor_view_naive_cutoff_rejected_before_io_depth(cutoff, monkeypatch, perf_budget):
    """FA-12: reject naive instants before I/O, including a failed DB.

    Local best-of-5/10,000 baseline: wall 0.000416 ms per rejection
    (Windows CPU timer rounded to zero). New wall budget 0.05 ms gives
    >120x headroom; virtual 10x-budget delay must trip the same assertion.
    No ledger writes occur, so journal replay is N/A for this guard scope.
    """
    from tests.support.perf_inject import virtual_delayed_sync

    with pytest.raises(NaiveCutoffError):
        await ibor_view.compute_ibor_view(
            pool=cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=datetime(2026, 1, 1)
        )

    class FailedPool:
        calls = 0

        def acquire(self):
            self.calls += 1
            raise ConnectionError("injected database outage")

    failed_pool = FailedPool()
    fund = uuid4()

    def reject_before_io():
        # Validation must finish synchronously, before the first DB await.
        coroutine = ibor_view.compute_ibor_view(
            pool=cast(asyncpg.Pool, failed_pool), fund_id=fund, cutoff=cutoff
        )
        error = None
        try:
            coroutine.send(None)
        except (NaiveCutoffError, ConnectionError, StopIteration) as exc:
            error = exc
        finally:
            coroutine.close()
        assert isinstance(error, NaiveCutoffError), "naive cutoff must fail before I/O"
        assert "cutoff" in str(error)
        assert failed_pool.calls == 0

    reject_before_io()
    sample = perf_budget.best_of(reject_before_io, n=5, batch=10000)
    budget_ms = 0.05

    def assert_budget(measurement):
        assert measurement.wall_ms < budget_ms, "naive rejection latency exceeded budget"

    assert_budget(sample)
    delayed = virtual_delayed_sync(reject_before_io, budget_ms * 10 / 1000)
    with pytest.raises(AssertionError, match="latency exceeded budget"):
        assert_budget(perf_budget.best_of(delayed, n=3, batch=100))

    # Gate red: remove the validation and prove the same oracle catches I/O.
    with monkeypatch.context() as patch:
        patch.setattr(ibor_view, "_require_tz_aware", lambda value, *, name: None)
        with pytest.raises(AssertionError, match="naive cutoff must fail before I/O"):
            reject_before_io()
    assert failed_pool.calls == 1
