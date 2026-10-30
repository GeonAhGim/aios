"""BT-11 성능·실패주입·게이트 적색 재현 테스트.

주요 책임:
- 수치 성능 단언 (PerfBudget 기반)
- 실패 주입 (인프라 장애 전파)
- 게이트 적색 재현 (회귀 감지)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application import deep_backtest_job as djob
from src.foundation.backtest.application import quick_backtest as quick_backtest_mod
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
from tests.conftest import PerfBudget

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
    """봉 인덱스(=len(window)-1) → 주문 의도. dict 조회만 하므로 결정론."""

    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


_PLAN = {
    50: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
    300: OrderIntent(side=OrderSide.SELL, quantity=_D("1")),
    700: OrderIntent(side=OrderSide.BUY, quantity=_D("1")),
}


# --- DEEPEN(task-3047): 수치 성능 단언 ------------------------------------

_DEEP_JOB_BUDGET_MS = 50.0


def _deep_job_latencies_ms(
    perf_budget: PerfBudget, iterations: int = 10, *, n_bars: int = 2000
) -> list[float]:
    # task-7434: process_time-based samples, not wall-clock perf_counter().
    cfg, cols = _config(), _columns(n_bars)

    def _run_once() -> None:
        run_deep_backtest_job(
            cfg,
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="perf",
            checkpoints=InMemoryCheckpointStore(),
            chunk_bars=200,
        )

    # batch=8: Windows process_time() quantization tick is 15.625ms.
    # PerfBudget.sample() docstring recommends batch>1 to reduce quantization
    # error from ~tick to ~tick/batch (task-6371, task-7253). batch=8 → ~2ms.
    samples = [s.cpu_ms for s in perf_budget.samples(_run_once, n=iterations, batch=8)]
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
def test_deep_job_p95_latency_within_self_declared_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표의 "백테스트 1개월
    M1 1심볼 3초"는 단발 실행 예산이라 이 리프(구간마다 접두 구간을 처음부터
    재실행하는 체크포인트 잡, O(n^2/chunk_bars))에 그대로 대입할 수 없다 —
    2000봉·chunk_bars=200(10개 청크, 접두 재실행 총량 11,000봉-등가 ≈ 단발
    실행의 5.5배)에 대한 자체 예산을 건다. 로컬 실측 p95 ~5.6ms(2026-09-16)
    대비 약 9배 여유를 둔 50ms. 예산을 벗어나면 청크 재실행 비용이 의도한
    O(n^2/chunk_bars)를 넘어서는 회귀(예: 매 청크가 전체 컬럼을 복사하는
    실수)로 본다."""
    samples = _deep_job_latencies_ms(perf_budget)
    # Parallel CI: p95 of 10 samples is the max, so one scheduler hiccup fails
    # the gate. A real O(n^2) regression inflates every sample, so the
    # best-of-N floor keeps detection power.
    p95_ms = _p95(samples)
    best_ms = samples[0]
    print(
        f"[BT-11] run_deep_backtest_job() best={best_ms:.2f}ms p95={p95_ms:.2f}ms "
        f"budget<{_DEEP_JOB_BUDGET_MS:.0f}ms"
    )
    assert best_ms < _DEEP_JOB_BUDGET_MS


@pytest.mark.perf
def test_perf_budget_gate_actually_fails_when_chunk_execution_stalls(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현: BT-10 실행 코어가 실제로 느려지면
    `test_deep_job_p95_latency_within_self_declared_budget`과 동일한 단언식이
    진짜로 `AssertionError`를 내는지(= CI가 빨간불이 되는지) 확인한다 — 그
    단언이 항상 통과하는 tautology가 아님을 보장한다. sleep() 지연 주입은
    process_time에 반영되지 않으므로 wall_ms로 측정한다(batch=1로 1회 측정:
    지연 주입으로 충분히 느려져 양자화 문제가 없다)."""
    original_run = quick_backtest_mod.run_quick_backtest

    def _stalled_run(*args: Any, **kwargs: Any) -> Any:
        time.sleep(_DEEP_JOB_BUDGET_MS / 1000)  # 청크 1개만으로도 예산 초과
        return original_run(*args, **kwargs)

    monkeypatch.setattr(djob, "run_quick_backtest", _stalled_run)

    cfg, cols = _config(), _columns(2000)
    store = InMemoryCheckpointStore()

    def _run_stalled_job() -> None:
        run_deep_backtest_job(
            cfg,
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="perf-stall",
            checkpoints=store,
            chunk_bars=200,
        )

    sample = perf_budget.sample(_run_stalled_job, batch=1)
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _DEEP_JOB_BUDGET_MS


# --- DEEPEN(task-3047): 실패 주입 ------------------------------------------


class _FlakyCheckpointStore:
    """두 번째 `save()` 호출에서 인프라 장애(예: DB 커넥션 끊김)를 흉내낸다."""

    def __init__(self) -> None:
        self._inner = InMemoryCheckpointStore()
        self._save_calls = 0

    def load(self, job_id: str) -> DeepBacktestCheckpoint | None:
        return self._inner.load(job_id)

    def save(self, job_id: str, checkpoint: DeepBacktestCheckpoint) -> None:
        self._save_calls += 1
        if self._save_calls == 2:
            raise ConnectionError("checkpoint store unavailable")
        self._inner.save(job_id, checkpoint)


def test_checkpoint_store_infra_failure_propagates_and_leaves_prior_checkpoint_intact() -> None:
    """실패 주입: `run_deep_backtest_job`은 `checkpoints.save()`를 감싸지
    않는다 — 두 번째 청크 저장에서 인프라 예외(`ConnectionError`)가 나면
    원래 타입 그대로 즉시 전파돼야 한다(taxonomy로 감싸거나 삼키지 않음).
    실패한 저장 시도가 이전에 이미 저장된 체크포인트(200봉)를 손상시키지
    않는지도 확인한다 — fail-closed: 절반쯤 실패한 청크가 다음 resume의
    출발점을 조용히 앞당기지 않는다. 정상 store로 교체해 재개하면 중단 없이
    끝까지 돌린 참조 실행과 바이트 동일한 결과에 도달함도 함께 확인한다."""
    cfg, cols = _config(), _columns(1000)

    flaky_store = _FlakyCheckpointStore()
    with pytest.raises(ConnectionError, match="checkpoint store unavailable"):
        run_deep_backtest_job(
            cfg,
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted(dict(_PLAN)),
            initial_cash=_CASH,
            job_id="job-flaky",
            checkpoints=flaky_store,
            chunk_bars=200,
        )
    intact = flaky_store.load("job-flaky")
    assert intact is not None and intact.bars_processed == 200

    resumed_store = InMemoryCheckpointStore()
    resumed_store.save("job-flaky", intact)
    resumed = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-flaky",
        checkpoints=resumed_store,
        chunk_bars=200,
    )
    assert resumed.status == "completed"

    reference_store = InMemoryCheckpointStore()
    reference = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-reference",
        checkpoints=reference_store,
        chunk_bars=200,
    )
    assert resumed.result == reference.result


# --- DEEPEN(task-3047): 게이트 적색 재현(바이트 동일 재개) ------------------


def test_byte_identical_gate_actually_fails_when_prefix_slicing_regresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `test_interrupt_then_resume_matches_uninterrupted_run
    _byte_for_byte`와 동일한 `... .result == full.result` 단언식이, 청chunk
    슬라이싱에 실제 회귀(예: 리팩터링 중 도입된 off-by-one으로 매 청크가
    마지막 1봉을 놓침)가 있으면 진짜로 `AssertionError`를 내는지 확인한다 —
    그 단언이 항상 통과하는 tautology가 아님을 보장한다. `bars_processed`
    자체를 조작하는 시도는 통하지 않는다 — 이 모듈은 매 청크에서 접두 구간
    전체(`_prefix(columns, end)`)를 처음부터 다시 실행하므로(재구현 없이
    BT-10을 호출, docstring 참고) 마지막 청크가 항상 `n`까지 도달하는 한
    결과가 재계산된다. 그래서 실제로 결과를 갈라놓을 수 있는 지점인
    `_prefix` 자체를 주입 대상으로 삼는다."""
    cfg, cols = _config(), _columns(1000)

    full_store = InMemoryCheckpointStore()
    full = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-full-2",
        checkpoints=full_store,
        chunk_bars=200,
    )

    original_prefix = djob._prefix  # noqa: SLF001

    def _off_by_one_prefix(columns: CandleColumns, end: int) -> CandleColumns:
        return original_prefix(columns, max(end - 1, 1))

    monkeypatch.setattr(djob, "_prefix", _off_by_one_prefix)

    bad_store = InMemoryCheckpointStore()
    resumed_bad = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-bad-prefix",
        checkpoints=bad_store,
        chunk_bars=200,
    )
    with pytest.raises(AssertionError):
        assert resumed_bad.result == full.result
