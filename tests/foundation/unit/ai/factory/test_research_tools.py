"""Unit tests for `src/foundation/ai/factory/application/research_tools.py`
-- task-2649 AI-14 DoD ("위임만, 로직 0"). D2 depth (ADR-2026-09-09-C):
negative >=3, failure injection 1, numeric performance assertion 1, gate-red
reproduction 1.

Every "정상 경로" test below proves delegation, not reimplementation, the
same way `tests/foundation/unit/backtest/test_quick_backtest.py`'s own
docstring frames BT-10's own proof: it calls the underlying leaf function
directly and asserts the wrapper returns the exact same value for the exact
same input.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import numpy as np
import pytest

from src.core.indicators.engine.vectorized import compute as compute_indicator_direct
from src.core.indicators.registry import IndicatorError
from src.data.models.trading import OrderSide
from src.foundation.ai.factory.application.research_tools import (
    ExperimentNotFoundError,
    NoReproductionsFoundError,
    ReproducibilityKeyMismatchError,
    TooManyBarsError,
    compare_experiment_reproductions,
    compare_experiment_set,
    compute_indicator,
    get_experiment_context,
    get_experiment_lineage,
    list_experiment_reproductions,
    run_backtest,
)
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
)
from src.foundation.backtest.application.quick_backtest import (
    run_quick_backtest as run_quick_backtest_direct,
)
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CASH = Decimal("100000")
_D = Decimal


# --- fakes -------------------------------------------------------------


class _FakeExperimentRepository:
    """In-memory `ExperimentRepository` -- same shape as
    `tests/foundation/unit/ai/factory/test_evaluate_proposal.py`'s own fake
    (this leaf adds no new persistence, only new callers of AI-11's
    already-tested query/compare use cases)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, UUID], Experiment] = {}
        self.get_calls = 0
        self.fail_get: BaseException | None = None

    async def append(self, experiment: Experiment) -> None:
        self._rows[(experiment.tenant_id, experiment.experiment_id)] = experiment

    async def get(self, tenant_id: UUID, experiment_id: UUID) -> Experiment | None:
        self.get_calls += 1
        if self.fail_get is not None:
            raise self.fail_get
        return self._rows.get((tenant_id, experiment_id))

    async def find_by_reproducibility_key(
        self, tenant_id: UUID, reproducibility_key: str
    ) -> tuple[Experiment, ...]:
        return tuple(
            e
            for e in self._rows.values()
            if e.tenant_id == tenant_id and e.reproducibility_key == reproducibility_key
        )


def _experiment(
    tenant_id: UUID, *, reproducibility_key: str, inputs_hash: str, parent_id: UUID | None = None
) -> Experiment:
    return Experiment(
        experiment_id=uuid4(),
        tenant_id=tenant_id,
        reproducibility_key=reproducibility_key,
        kind=ExperimentKind.BACKTEST,
        inputs_hash=inputs_hash,
        metrics={"sharpe_ratio": 1.2},
        parent_id=parent_id,
        created_by=uuid4(),
        created_at=_T0,
    )


class _Scripted:
    """봉 인덱스 -> 주문 의도. `test_quick_backtest.py`와 동일한 결정론적 fake."""

    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


def _columns(closes: list[str]) -> CandleColumns:
    c = [_D(x) for x in closes]
    o = [c[0], *c[:-1]]
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(len(c))],
        open=o,
        high=[max(a, b) + 1 for a, b in zip(o, c, strict=True)],
        low=[min(a, b) - 1 for a, b in zip(o, c, strict=True)],
        close=c,
        volume=[_D("1000")] * len(c),
        quote_volume=[None] * len(c),
    )


def _backtest_config(**overrides: object) -> BacktestConfigV2:
    base: dict[str, object] = dict(
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
    return BacktestConfigV2.model_validate(base)


_CLOSES = ["100", "101", "102", "103", "104", "105", "106", "107"]
_BUY10 = OrderIntent(side=OrderSide.BUY, quantity=_D("10"))


# --- 정상 경로: 위임 증명 ------------------------------------------------


def test_compute_indicator_delegates_to_engine_vectorized() -> None:
    columns = {"close": [float(x) for x in range(1, 40)]}
    params = {"timeperiod": 5}

    wrapped = compute_indicator("SMA", columns, params)
    direct = compute_indicator_direct("SMA", columns, params)

    assert wrapped.keys() == direct.keys()
    for key in wrapped:
        assert np.array_equal(wrapped[key], direct[key], equal_nan=True)


def test_run_backtest_delegates_to_quick_backtest() -> None:
    cfg, cols = _backtest_config(), _columns(_CLOSES)
    plan = {2: _BUY10}

    wrapped = run_backtest(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted(plan), initial_cash=_CASH
    )
    direct = run_quick_backtest_direct(
        cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted(plan), initial_cash=_CASH
    )

    assert wrapped.fills == direct.fills
    assert wrapped.equity_curve == direct.equity_curve
    assert wrapped.final_equity == direct.final_equity


@pytest.mark.asyncio
async def test_experiment_context_functions_delegate_to_ai11_query_and_compare() -> None:
    repo = _FakeExperimentRepository()
    tenant_id = uuid4()
    key = "a" * 64
    inputs_hash = "b" * 64
    parent = _experiment(tenant_id, reproducibility_key="c" * 64, inputs_hash="d" * 64)
    await repo.append(parent)
    child = _experiment(
        tenant_id, reproducibility_key=key, inputs_hash=inputs_hash, parent_id=parent.experiment_id
    )
    await repo.append(child)
    sibling = _experiment(tenant_id, reproducibility_key=key, inputs_hash=inputs_hash)
    await repo.append(sibling)

    fetched = await get_experiment_context(repo, tenant_id, child.experiment_id)
    assert fetched == child

    reproductions = await list_experiment_reproductions(repo, tenant_id, key)
    assert {e.experiment_id for e in reproductions} == {child.experiment_id, sibling.experiment_id}

    lineage = await get_experiment_lineage(repo, tenant_id, child.experiment_id)
    assert lineage == (parent, child)

    by_ids = await compare_experiment_set(
        repo, tenant_id, [child.experiment_id, sibling.experiment_id]
    )
    assert by_ids.reproducibility_key == key
    assert by_ids.inputs_hash == inputs_hash

    by_key = await compare_experiment_reproductions(repo, tenant_id, key)
    assert {e.experiment_id for e in by_key.experiments} == {
        child.experiment_id,
        sibling.experiment_id,
    }


# --- 부정 테스트 (>=3) ---------------------------------------------------


def test_compute_indicator_propagates_unknown_indicator_error() -> None:
    with pytest.raises(IndicatorError) as exc_info:
        compute_indicator("NOT_A_REAL_INDICATOR", {"close": [1.0, 2.0, 3.0]})
    assert exc_info.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_run_backtest_propagates_too_many_bars_error() -> None:
    cfg, cols = _backtest_config(), _columns(_CLOSES)
    with pytest.raises(TooManyBarsError):
        run_backtest(
            cfg,
            cols,
            timeframe=Timeframe.M1,
            strategy=_Scripted({}),
            initial_cash=_CASH,
            max_bars=1,
        )


@pytest.mark.asyncio
async def test_get_experiment_context_propagates_not_found() -> None:
    repo = _FakeExperimentRepository()
    with pytest.raises(ExperimentNotFoundError):
        await get_experiment_context(repo, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_compare_experiment_set_propagates_reproducibility_key_mismatch() -> None:
    repo = _FakeExperimentRepository()
    tenant_id = uuid4()
    first = _experiment(tenant_id, reproducibility_key="e" * 64, inputs_hash="f" * 64)
    second = _experiment(tenant_id, reproducibility_key="0" * 64, inputs_hash="f" * 64)
    await repo.append(first)
    await repo.append(second)

    with pytest.raises(ReproducibilityKeyMismatchError):
        await compare_experiment_set(repo, tenant_id, [first.experiment_id, second.experiment_id])


@pytest.mark.asyncio
async def test_compare_experiment_reproductions_propagates_no_reproductions_found() -> None:
    repo = _FakeExperimentRepository()
    with pytest.raises(NoReproductionsFoundError):
        await compare_experiment_reproductions(repo, uuid4(), "1" * 64)


# --- 실패 주입 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_experiment_context_propagates_repository_failure() -> None:
    """실패 주입: DB 장애 등으로 repository.get()이 예외를 내면 조용히
    삼키지 않고 그대로 전파돼야 한다 -- 위임 계층이 오류를 성공으로
    위장하지 않는다."""
    repo = _FakeExperimentRepository()
    repo.fail_get = ConnectionError("experiment ledger unreachable")
    with pytest.raises(ConnectionError):
        await get_experiment_context(repo, uuid4(), uuid4())


# --- 수치 성능 단언 -------------------------------------------------------

_RUN_BACKTEST_BUDGET_MS = 200.0
"""§7 SLO의 research(백테스트 1개월) <=5s보다 훨씬 좁게 잡는다: 여기서 도는
표본은 8개 봉짜리 인메모리 백테스트라 순수 위임 오버헤드만 측정한다."""


def _run_backtest_latencies_ms(iterations: int = 20) -> list[float]:
    cfg, cols = _backtest_config(), _columns(_CLOSES)
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        run_backtest(
            cfg, cols, timeframe=Timeframe.M1, strategy=_Scripted({2: _BUY10}), initial_cash=_CASH
        )
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_run_backtest_p95_latency_within_budget() -> None:
    samples = _run_backtest_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[AI-14 run_backtest] p95={p95_ms:.2f}ms budget<{_RUN_BACKTEST_BUDGET_MS:.0f}ms")
    assert p95_ms < _RUN_BACKTEST_BUDGET_MS


# --- 게이트 적색 재현 -----------------------------------------------------


def test_gate_red_budget_actually_fails_past_budget() -> None:
    samples = _run_backtest_latencies_ms(iterations=5)
    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms
