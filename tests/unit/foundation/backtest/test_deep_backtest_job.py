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
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CASH = Decimal("100000")
_D = Decimal


def _columns(n: int) -> CandleColumns:
    closes = [_D(100) + _D(i % 7) for i in range(n)]
    opens = [closes[0], *closes[:-1]] if closes else []
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(n)], open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)], close=closes,
        volume=[_D("1000")] * n, quote_volume=[None] * n,
    )


def _config(**overrides: object) -> BacktestConfigV2:
    base: dict[str, object] = dict(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0, partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None, costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False), calendar="24x7",
    )
    base.update(overrides)
    return BacktestConfigV2(**base)  # type: ignore[arg-type]


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
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH, job_id="job-full", checkpoints=full_store, chunk_bars=200,
    )
    assert full.status == "completed"
    assert full.result is not None

    resumed_store = InMemoryCheckpointStore()
    calls = {"n": 0}

    def _stop_after_two_chunks() -> bool:
        calls["n"] += 1
        return calls["n"] <= 2  # 200봉·400봉까지만 허용, 세 번째(600봉)에서 중단

    interrupted = run_deep_backtest_job(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH, job_id="job-resumed", checkpoints=resumed_store, chunk_bars=200,
        should_continue=_stop_after_two_chunks,
    )
    assert interrupted.status == "suspended"
    assert interrupted.checkpoint is not None
    assert interrupted.checkpoint.bars_processed == 400

    resumed = run_deep_backtest_job(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted(dict(_PLAN)),
        initial_cash=_CASH, job_id="job-resumed", checkpoints=resumed_store, chunk_bars=200,
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
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted({}), initial_cash=_CASH,
        job_id="job-mismatch", checkpoints=store, chunk_bars=200,
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
            changed_cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted({}),
            initial_cash=_CASH, job_id="job-mismatch", checkpoints=store, chunk_bars=200,
        )
    # 거부 후에도 저장된 체크포인트는 그대로다 — 조용히 덮어써 재시작하지 않는다.
    stored = store.load("job-mismatch")
    assert stored is not None and stored.bars_processed == 200


def test_progress_is_monotonic_and_interruption_progress_is_point_four() -> None:
    cfg, cols = _config(), _columns(1000)
    store = InMemoryCheckpointStore()
    seen: list[float] = []

    run_deep_backtest_job(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted({}), initial_cash=_CASH,
        job_id="job-progress", checkpoints=store, chunk_bars=200, on_progress=seen.append,
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
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted({}), initial_cash=_CASH,
        job_id="job-progress-2", checkpoints=interrupt_store, chunk_bars=200,
        should_continue=_stop_after_two_chunks,
    )
    assert outcome.checkpoint is not None
    assert abs(outcome.checkpoint.progress - 0.4) <= 0.01


def test_checkpoint_progress_property_is_bars_processed_over_total() -> None:
    checkpoint = DeepBacktestCheckpoint(
        config_hash="c" * 64, last_processed_ts=_T0, bars_processed=400, total_bars=1000,
        state_hash="d" * 64,
    )
    assert checkpoint.progress == 0.4


def test_does_not_reimplement_fill_or_cost_math() -> None:
    """§9.5 BT-11 DoD (d): 이 모듈에 체결·수수료·슬리피지 산식이 없고
    BT-10(`run_quick_backtest`)만 호출한다 — Decimal 리터럴도 부동소수
    리터럴도 정의하지 않는다(모두 BT-2~8/BT-10에서 흘러온 값이다)."""
    source = inspect.getsource(djob)
    for forbidden in (
        "apply_slippage", "compute_commission", "compute_partial_fill", "magnify(",
        "compute_funding_cost", "compute_borrow_cost", "is_limit_triggered",
        "is_stop_triggered", "resolve_execution_bar_index",
    ):
        assert forbidden not in source, f"재구현 의심: {forbidden}가 소스에 등장한다"
    assert "Decimal(" not in source
    float_literals = [
        node.value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert float_literals == [], f"부동소수 리터럴은 체결·비용 모델의 흔적이다: {float_literals}"
    assert "run_quick_backtest" in source


def test_empty_columns_and_blank_job_id_are_rejected() -> None:
    cfg = _config()
    store = InMemoryCheckpointStore()
    with pytest.raises(ValueError, match="캔들이 0개"):
        run_deep_backtest_job(
            cfg, _columns(0), timeframe=Timeframe.M1, strategy=_Scripted({}),
            initial_cash=_CASH, job_id="job-x", checkpoints=store,
        )
    with pytest.raises(ValueError, match="job_id"):
        run_deep_backtest_job(
            cfg, _columns(10), timeframe=Timeframe.M1, strategy=_Scripted({}),
            initial_cash=_CASH, job_id="   ", checkpoints=store,
        )
