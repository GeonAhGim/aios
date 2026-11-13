"""BT-11 D2 depth supplement: negative, failure-injection, adversarial, perf, gate-red.

Complements `test_deep_backtest_job.py` / `test_deep_backtest_job_perf.py` without touching them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application import deep_backtest_job as djob
from src.foundation.backtest.application.deep_backtest_job import (
    BacktestCheckpointMismatchError,
    DeepBacktestCheckpoint,
    InMemoryCheckpointStore,
    run_deep_backtest_job,
)
from src.foundation.backtest.application.quick_backtest import BarWindow, PositionState
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CASH = Decimal("100000")
_D = Decimal


def _columns(n: int) -> CandleColumns:
    closes = [_D(100) + _D(i % 7) for i in range(n)]
    opens = [closes[0], *closes[:-1]] if closes else []
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(n)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _config(**overrides: Any) -> BacktestConfigV2:
    base: dict[str, Any] = dict(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )
    base.update(overrides)
    return BacktestConfigV2(**base)


class _Scripted:
    def __init__(self, plan: dict[int, OrderIntent], *, boom_at: int | None = None) -> None:
        self.plan = plan
        self.boom_at = boom_at

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        idx = len(window) - 1
        if self.boom_at is not None and idx == self.boom_at:
            raise RuntimeError("strategy crashed")
        return self.plan.get(idx)


_PLAN = {
    50: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
    300: OrderIntent(side=OrderSide.SELL, quantity=_D("1")),
    700: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
}


def _run(
    store: InMemoryCheckpointStore,
    *,
    job_id: str = "job",
    n: int = 1000,
    chunk_bars: int = 200,
    strategy: _Scripted | None = None,
    cfg: BacktestConfigV2 | None = None,
    **kwargs: Any,
) -> Any:
    return run_deep_backtest_job(
        cfg or _config(),
        _columns(n),
        timeframe=Timeframe.M1,
        strategy=strategy or _Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id=job_id,
        checkpoints=store,
        chunk_bars=chunk_bars,
        **kwargs,
    )


def _checkpoint(cfg: BacktestConfigV2, bars: int, total: int = 1000) -> DeepBacktestCheckpoint:
    return DeepBacktestCheckpoint(
        config_hash=djob.compute_config_hash(cfg),
        last_processed_ts=_T0,
        bars_processed=bars,
        total_bars=total,
        state_hash="f" * 64,
    )


@pytest.mark.parametrize("chunk_bars", [0, -1, -200])
def test_non_positive_chunk_bars_rejected_without_saving(chunk_bars: int) -> None:
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="chunk_bars"):
        _run(store, chunk_bars=chunk_bars)
    assert store.load("job") is None


@pytest.mark.parametrize("job_id", ["", " ", "\t\n"])
def test_blank_job_id_variants_rejected_without_saving(job_id: str) -> None:
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="job_id"):
        _run(store, job_id=job_id)
    assert store.load(job_id) is None


def test_resume_rejected_when_only_slippage_changed() -> None:
    store = InMemoryCheckpointStore()
    store.save("job", _checkpoint(_config(), 200))
    with pytest.raises(BacktestCheckpointMismatchError):
        _run(store, cfg=_config(slippage=FixedSlippage(bps=_D("11"))))
    stored = store.load("job")
    assert stored is not None and stored.bars_processed == 200


def test_failure_injection_strategy_crash_keeps_prior_checkpoint_and_resume_matches() -> None:
    store = InMemoryCheckpointStore()
    with pytest.raises(RuntimeError, match="strategy crashed"):
        _run(store, strategy=_Scripted(dict(_PLAN), boom_at=450))
    stored = store.load("job")
    assert stored is not None and stored.bars_processed == 400

    resumed = _run(store)
    reference = _run(InMemoryCheckpointStore(), job_id="ref")
    assert resumed.status == "completed"
    assert resumed.result == reference.result


def test_failure_injection_progress_callback_error_after_save_is_resumable() -> None:
    store = InMemoryCheckpointStore()

    def _boom(progress: float) -> None:
        raise OSError("progress sink down")

    with pytest.raises(OSError, match="progress sink down"):
        _run(store, on_progress=_boom)
    stored = store.load("job")
    assert stored is not None and stored.bars_processed == 200

    resumed = _run(store)
    assert resumed.status == "completed"
    assert resumed.checkpoint is not None and resumed.checkpoint.bars_processed == 1000


def test_failure_injection_should_continue_error_leaves_store_untouched() -> None:
    store = InMemoryCheckpointStore()

    def _boom() -> bool:
        raise TimeoutError("cancel flag lookup failed")

    with pytest.raises(TimeoutError):
        _run(store, should_continue=_boom)
    assert store.load("job") is None


def test_adversarial_forged_progress_checkpoint_cannot_change_final_result() -> None:
    """Fail-closed: a forged checkpoint (matching hash, bogus state_hash, bars_processed
    possibly beyond the data) must not skew the result; state is re-derived from bars."""
    reference = _run(InMemoryCheckpointStore(), job_id="ref")
    for bars in (200, 999, 1000, 5000):
        store = InMemoryCheckpointStore()
        store.save("job", _checkpoint(_config(), bars))
        outcome = _run(store)
        assert outcome.status == "completed"
        assert outcome.result == reference.result
        assert outcome.checkpoint is not None and outcome.checkpoint.bars_processed == 1000
        assert outcome.checkpoint.state_hash != "f" * 64


def test_gate_red_state_hash_inequality_assertion_catches_corrupted_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red repro: a digest that ignores the run makes distinct runs look identical; the
    state-hash inequality assertion must then fail, proving it is not a tautology."""
    with_fills = _run(InMemoryCheckpointStore(), job_id="a")
    without_fills = _run(InMemoryCheckpointStore(), job_id="b", strategy=_Scripted({}))
    assert with_fills.checkpoint is not None and without_fills.checkpoint is not None
    assert with_fills.checkpoint.state_hash != without_fills.checkpoint.state_hash

    monkeypatch.setattr(djob, "_state_digest", lambda result: "0" * 64)
    broken_a = _run(InMemoryCheckpointStore(), job_id="a")
    broken_b = _run(InMemoryCheckpointStore(), job_id="b", strategy=_Scripted({}))
    assert broken_a.checkpoint is not None and broken_b.checkpoint is not None
    with pytest.raises(AssertionError):
        assert broken_a.checkpoint.state_hash != broken_b.checkpoint.state_hash


_RESUME_REJECT_BUDGET_MS = 25.0


def _reject_once(store: InMemoryCheckpointStore) -> None:
    with pytest.raises(BacktestCheckpointMismatchError):
        _run(store, cfg=_config(latency_ms=1))


@pytest.mark.perf
def test_forged_checkpoint_rejection_best_of_within_budget(perf_budget: PerfBudget) -> None:
    """Perf: rejecting a foreign-config resume (load + hash compare, no bar processing) stays
    under 25 ms CPU, best_of(n=7, batch=4).
    Local measurement 2026-11-14: wall ~0.7 ms per rejection, CPU below the 15.6 ms Windows tick
    (reads 0.0); 25 ms is ~35x the wall measurement."""
    store = InMemoryCheckpointStore()
    store.save("job", _checkpoint(_config(), 200))
    sample = perf_budget.best_of(lambda: _reject_once(store), n=7, batch=4)
    assert sample.cpu_ms < _RESUME_REJECT_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_RESUME_REJECT_BUDGET_MS
    )


@pytest.mark.perf
def test_forged_checkpoint_rejection_virtual_delay_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red repro: a 250 ms (10x budget) virtual delay on config hashing turns it red."""
    reset_virtual_totals()
    monkeypatch.setattr(
        djob, "compute_config_hash", virtual_delayed_sync(djob.compute_config_hash, 0.25)
    )
    store = InMemoryCheckpointStore()
    store.save("job", _checkpoint(_config(), 200))
    sample = perf_budget.best_of(lambda: _reject_once(store), n=3, batch=1)
    assert sample.cpu_ms >= 250.0
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _RESUME_REJECT_BUDGET_MS
    reset_virtual_totals()
