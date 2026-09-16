"""BT-11 `application/deep_backtest_job.py` — 체크포인트·재개·진행률(순수, DB 없음).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5
BT-11, §9.5 BT-11. DoD 검증: (a) 중단·재개 결과가 한 번에 끝까지 돌린
결과와 바이트 동일, (b) config_hash가 다른 resume은 fail-closed 거부,
(c) 진행률 단조증가·마지막 1.0·중단 지점 0.4±0.01, (d) 체결·비용 모델을
재구현하지 않고 BT-10(`run_quick_backtest`)만 호출한다.
"""

from __future__ import annotations

import ast
import inspect
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application import deep_backtest_job as djob
from src.foundation.backtest.application import quick_backtest as quick_backtest_mod
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

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
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


def test_interrupt_then_resume_matches_uninterrupted_run_byte_for_byte() -> None:
    cfg, cols = _config(), _columns(1000)

    full_store = InMemoryCheckpointStore()
    full = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-full",
        checkpoints=full_store,
        chunk_bars=200,
    )
    assert full.status == "completed"
    assert full.result is not None

    resumed_store = InMemoryCheckpointStore()
    calls = {"n": 0}

    def _stop_after_two_chunks() -> bool:
        calls["n"] += 1
        return calls["n"] <= 2  # 200봉·400봉까지만 허용, 세 번째(600봉)에서 중단

    interrupted = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-resumed",
        checkpoints=resumed_store,
        chunk_bars=200,
        should_continue=_stop_after_two_chunks,
    )
    assert interrupted.status == "suspended"
    assert interrupted.checkpoint is not None
    assert interrupted.checkpoint.bars_processed == 400

    resumed = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH,
        job_id="job-resumed",
        checkpoints=resumed_store,
        chunk_bars=200,
    )
    assert resumed.status == "completed"
    assert resumed.result == full.result
    assert djob._state_digest(resumed.result) == djob._state_digest(full.result)  # noqa: SLF001


def test_resume_with_mismatched_config_hash_is_rejected_fail_closed() -> None:
    cfg, cols = _config(), _columns(1000)
    store = InMemoryCheckpointStore()
    calls = {"n": 0}

    def _stop_after_one_chunk() -> bool:
        calls["n"] += 1
        return calls["n"] <= 1  # 200봉까지만 허용, 두 번째(400봉)에서 중단

    first = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted({}),
        initial_cash=_CASH,
        job_id="job-mismatch",
        checkpoints=store,
        chunk_bars=200,
        should_continue=_stop_after_one_chunk,
    )
    assert first.status == "suspended"

    changed_cfg = _config(
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0.001")
        )
    )
    with pytest.raises(BacktestCheckpointMismatchError):
        run_deep_backtest_job(
            changed_cfg,
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="job-mismatch",
            checkpoints=store,
            chunk_bars=200,
        )
    # 거부 후에도 저장된 체크포인트는 그대로다 — 조용히 덮어써 재시작하지 않는다.
    stored = store.load("job-mismatch")
    assert stored is not None and stored.bars_processed == 200


def test_progress_is_monotonic_and_interruption_progress_is_point_four() -> None:
    cfg, cols = _config(), _columns(1000)
    store = InMemoryCheckpointStore()
    seen: list[float] = []

    run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted({}),
        initial_cash=_CASH,
        job_id="job-progress",
        checkpoints=store,
        chunk_bars=200,
        on_progress=seen.append,
    )
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    assert seen[0] > 0.0

    interrupt_store = InMemoryCheckpointStore()
    calls = {"n": 0}

    def _stop_after_two_chunks() -> bool:
        calls["n"] += 1
        return calls["n"] <= 2

    outcome = run_deep_backtest_job(
        cfg,
        cols,
        timeframe=Timeframe.M1,
        strategy=_Scripted({}),
        initial_cash=_CASH,
        job_id="job-progress-2",
        checkpoints=interrupt_store,
        chunk_bars=200,
        should_continue=_stop_after_two_chunks,
    )
    assert outcome.checkpoint is not None
    assert abs(outcome.checkpoint.progress - 0.4) <= 0.01


def test_checkpoint_progress_property_is_bars_processed_over_total() -> None:
    checkpoint = DeepBacktestCheckpoint(
        config_hash="c" * 64,
        last_processed_ts=_T0,
        bars_processed=400,
        total_bars=1000,
        state_hash="d" * 64,
    )
    assert checkpoint.progress == 0.4


def test_does_not_reimplement_fill_or_cost_math() -> None:
    """§9.5 BT-11 DoD (d): 이 모듈에 체결·수수료·슬리피지 산식이 없고
    BT-10(`run_quick_backtest`)만 호출한다 — Decimal 리터럴도 부동소수
    리터럴도 정의하지 않는다(모두 BT-2~8/BT-10에서 흘러온 값이다)."""
    source = inspect.getsource(djob)
    for forbidden in (
        "apply_slippage",
        "compute_commission",
        "compute_partial_fill",
        "magnify(",
        "compute_funding_cost",
        "compute_borrow_cost",
        "is_limit_triggered",
        "is_stop_triggered",
        "resolve_execution_bar_index",
    ):
        assert forbidden not in source, f"재구현 의심: {forbidden}가 소스에 등장한다"
    assert "Decimal(" not in source
    float_literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert float_literals == [], f"부동소수 리터럴은 체결·비용 모델의 흔적이다: {float_literals}"
    assert "run_quick_backtest" in source


def test_empty_columns_and_blank_job_id_are_rejected() -> None:
    cfg = _config()
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="캔들이 0개"):
        run_deep_backtest_job(
            cfg,
            _columns(0),
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="job-x",
            checkpoints=store,
        )
    with pytest.raises(ValueError, match="job_id"):
        run_deep_backtest_job(
            cfg,
            _columns(10),
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id="   ",
            checkpoints=store,
        )


# --- DEEPEN(task-3047): 수치 성능 단언 ------------------------------------

_DEEP_JOB_BUDGET_MS = 50.0


def _deep_job_latencies_ms(iterations: int = 10, *, n_bars: int = 2000) -> list[float]:
    cfg, cols = _config(), _columns(n_bars)
    samples: list[float] = []
    for i in range(iterations):
        store = InMemoryCheckpointStore()
        started = time.perf_counter()
        run_deep_backtest_job(
            cfg,
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            job_id=f"perf-{i}",
            checkpoints=store,
            chunk_bars=200,
        )
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_deep_job_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표의 "백테스트 1개월
    M1 1심볼 3초"는 단발 실행 예산이라 이 리프(구간마다 접두 구간을 처음부터
    재실행하는 체크포인트 잡, O(n^2/chunk_bars))에 그대로 대입할 수 없다 —
    2000봉·chunk_bars=200(10개 청크, 접두 재실행 총량 11,000봉-등가 ≈ 단발
    실행의 5.5배)에 대한 자체 예산을 건다. 로컬 실측 p95 ~5.6ms(2026-09-16)
    대비 약 9배 여유를 둔 50ms. 예산을 벗어나면 청크 재실행 비용이 의도한
    O(n^2/chunk_bars)를 넘어서는 회귀(예: 매 청크가 전체 컬럼을 복사하는
    실수)로 본다."""
    samples = _deep_job_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[BT-11] run_deep_backtest_job() p95={p95_ms:.2f}ms budget<{_DEEP_JOB_BUDGET_MS:.0f}ms")
    assert p95_ms < _DEEP_JOB_BUDGET_MS


def test_perf_budget_gate_actually_fails_when_chunk_execution_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: BT-10 실행 코어가 실제로 느려지면
    `test_deep_job_p95_latency_within_self_declared_budget`과 동일한 단언식이
    진짜로 `AssertionError`를 내는지(= CI가 빨간불이 되는지) 확인한다 — 그
    단언이 항상 통과하는 tautology가 아님을 보장한다."""
    original_run = quick_backtest_mod.run_quick_backtest

    def _stalled_run(*args: Any, **kwargs: Any) -> Any:
        time.sleep(_DEEP_JOB_BUDGET_MS / 1000)  # 청크 1개만으로도 예산 초과
        return original_run(*args, **kwargs)

    monkeypatch.setattr(djob, "run_quick_backtest", _stalled_run)

    cfg, cols = _config(), _columns(2000)
    store = InMemoryCheckpointStore()
    started = time.perf_counter()
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
    elapsed_ms = (time.perf_counter() - started) * 1000
    with pytest.raises(AssertionError):
        assert elapsed_ms < _DEEP_JOB_BUDGET_MS


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
    _byte_for_byte`와 동일한 `... .result == full.result` 단언식이, 청크
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
