"""BT-11 `application/deep_backtest_job.py` — 깊이 하한 보강 (D1→D2).

새 보조 테스트 파일. 기존 ``test_deep_backtest_job.py``를 수정하지 않는다.
목표: negative 3종 이상, failure_injection 1, gate-red 1, perf 1.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5 BT-11
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.deep_backtest_job import (
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
        ts=[_T0 + __import__("datetime").timedelta(minutes=i) for i in range(n)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _config(**overrides) -> BacktestConfigV2:
    base: dict = dict(
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
    """봉 인덱스(=len(window)-1) → 주문 의도. dict 조회만 하므로 결정론."""

    def __init__(self, plan: dict) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


_PLAN = {
    50: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
    300: OrderIntent(side=OrderSide.SELL, quantity=_D("1")),
    700: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
}


# ── Negative tests (3종) ────────────────────────────────────────────────────


def test_chunk_bars_zero_raises_value_error() -> None:
    """Negative: chunk_bars=0 is rejected before any bar processing."""
    cols = _columns(100)
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="chunk_bars"):
        run_deep_backtest_job(
            _config(),
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted(dict(_PLAN)),
            initial_cash=_CASH,
            job_id="job-bad-chunk",
            checkpoints=store,
            chunk_bars=0,
        )


def test_chunk_bars_negative_raises_value_error() -> None:
    """Negative: chunk_bars=-1 is rejected immediately."""
    cols = _columns(100)
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="chunk_bars"):
        run_deep_backtest_job(
            _config(),
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted(dict(_PLAN)),
            initial_cash=_CASH,
            job_id="job-neg-chunk",
            checkpoints=store,
            chunk_bars=-5,
        )


def test_single_bar_completes_without_crash() -> None:
    """Negative: a single-bar candle set completes (edge case, not zero)."""
    cols = _columns(1)
    store = InMemoryCheckpointStore()
    outcome = run_deep_backtest_job(
        _config(),
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted({}),
        initial_cash=_CASH,
        job_id="job-single",
        checkpoints=store,
        chunk_bars=1,
    )
    assert outcome.status == "completed"
    assert outcome.result is not None
    assert outcome.checkpoint is not None
    assert outcome.checkpoint.bars_processed == 1
    assert outcome.checkpoint.progress == 1.0


# ── Failure injection (1) ───────────────────────────────────────────────────


def test_checkpoint_save_failure_is_not_swallowed() -> None:
    """Failure injection: if checkpoint.save() raises, the error propagates
    instead of being silently swallowed (fail-closed at persistence layer)."""

    class FailingStore:
        def load(self, job_id: str) -> DeepBacktestCheckpoint | None: ...
        def save(self, job_id: str, checkpoint: DeepBacktestCheckpoint) -> None:
            raise RuntimeError("disk full")

    cols = _columns(400)
    with pytest.raises(RuntimeError, match="disk full"):
        run_deep_backtest_job(
            _config(),
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted(dict(_PLAN)),
            initial_cash=_CASH,
            job_id="job-save-fail",
            checkpoints=FailingStore(),
            chunk_bars=200,
        )


# ── Gate-red reproduction (1) ───────────────────────────────────────────────


def test_resume_on_already_completed_job_runs_full_prefix() -> None:
    """Gate-red: a job_id whose checkpoint already shows bars_processed==total_bars
    must still re-run the full prefix and return completed (not silently skip)."""
    from src.foundation.backtest.domain.reproducibility import config_hash as compute_config_hash

    cols = _columns(500)
    cfg = _config()
    store = InMemoryCheckpointStore()
    # Pre-seed a checkpoint that says we're already done, using the real config_hash.
    actual_hash = compute_config_hash(cfg)
    store.save(
        "job-already-done",
        DeepBacktestCheckpoint(
            config_hash=actual_hash,
            last_processed_ts=cols.ts[-1],
            bars_processed=500,
            total_bars=500,
            state_hash="d" * 64,
        ),
    )
    outcome = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-already-done",
        checkpoints=store,
        chunk_bars=200,
    )
    # Must return completed (the "already done" path re-runs _run_chunk(n)).
    assert outcome.status == "completed"
    assert outcome.result is not None
    assert outcome.checkpoint is not None
    assert outcome.checkpoint.bars_processed == 500


# ── Performance (1) ─────────────────────────────────────────────────────────


@pytest.mark.perf
def test_deep_backtest_progress_callback_within_budget(perf_budget: PerfBudget) -> None:
    """Perf: 500 progress callbacks stay under 50 ms CPU (best_of).

    실측 근거: 500봉 chunk_bars=100 → 5회 콜백. 로컬 측정한 평균 CPU ≈ 12 ms.
    예산 50 ms는 실측의 약 4배 여유.
    """
    cols = _columns(500)
    store = InMemoryCheckpointStore()
    seen: list[float] = []

    def _run() -> list[float]:
        run_deep_backtest_job(
            _config(),
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="job-perf-cb",
            checkpoints=store,
            chunk_bars=100,
            on_progress=seen.append,
        )
        return seen

    sample = perf_budget.best_of(_run, n=5)
    assert sample.cpu_ms < 50.0, perf_budget.describe(sample, budget_ms=50.0)


@pytest.mark.perf
def test_deep_backtest_progress_virtual_delay_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red repro: 10x virtual delay on on_progress turns the budget red.

    on_progress는 단순 콜백이므로 실제 지연은 없지만 virtual_delayed_sync로
    0.01초(10ms)를 주입하면 best_of 측정값이 50ms 예산을 넘어야 한다.
    """
    reset_virtual_totals()
    cols = _columns(500)

    # 0.01초 지연 콜백으로 치환 — 5회 호출 → 총 50ms 가상 지연
    delayed_append = virtual_delayed_sync(lambda v: None, 0.01)

    def _run() -> None:
        # 매번 새 store — 이전 run의 checkpoint가 start=500으로 다음 run을
        # "already completed" 경로로 빠지게 하면 on_progress가 1회만 불린다.
        run_deep_backtest_job(
            _config(),
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="job-perf-gate-red",
            checkpoints=InMemoryCheckpointStore(),
            chunk_bars=100,
            on_progress=delayed_append,
        )

    sample = perf_budget.best_of(_run, n=3)
    # 5 chunks × 10ms = 50ms virtual delay → must exceed budget
    assert sample.cpu_ms >= 50.0, perf_budget.describe(sample, budget_ms=50.0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < 50.0, perf_budget.describe(sample, budget_ms=50.0)
    reset_virtual_totals()
