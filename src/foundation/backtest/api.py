"""Backtest 애그리게잇의 top-level 재노출 모듈 — task-5417 (task-5404 분할 3/4).

`contracts/v1.py`는 107번(계약 버저닝) 관례상 domain을 import하지 않는 순수
pydantic 재선언이라, 다른 bounded context가 실제 domain 타입(`BacktestConfig`,
`BacktestResult` 등)·adapter 클래스(`ListBars`)·순수 함수(`make_splits`,
`stability_score` 등)를 그대로 재사용해야 하는 자리에는 맞지 않는다. 이 파일이
그 자리를 메운다 — `backtest.domain`/`backtest.adapters` 내부 경로 대신 이
모듈을 통해서만 다른 애그리게잇이 접근한다(import-linter
`boundary:foundation-aggregates`, task-5404/5417).

같은 애그리게잇 내부에서 domain/adapters를 import하는 것은 경계 위반이
아니므로(교차 애그리게잇일 때만 검사됨) 여기서는 그대로 재노출만 한다 —
객체 identity가 그대로 유지되어 런타임 동작은 바뀌지 않는다.
"""
from __future__ import annotations

from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    CostModel,
)
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.domain.overfitting import (
    OverfittingError,
    deflated_sharpe,
    pbo_cscv,
)
from src.foundation.backtest.domain.param_stability import (
    ParamGrid,
    ParamStabilityError,
    Point,
    stability_score,
)
from src.foundation.backtest.domain.rules import (
    CostModelRequiredError,
    LookaheadViolationError,
    require_cost_model,
)
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.splits import (
    MinTrainUnsatisfiableError,
    OosLeakageError,
    assert_no_overlap,
    make_splits,
)
from src.foundation.backtest.domain.universe import UniverseSnapshot

__all__ = [
    "BacktestConfig",
    "BacktestConfigV2",
    "BacktestMetrics",
    "BacktestResult",
    "BarSnapshotRef",
    "CostModel",
    "CostModelRequiredError",
    "ListBars",
    "LookaheadViolationError",
    "MinTrainUnsatisfiableError",
    "OosLeakageError",
    "OverfittingError",
    "ParamGrid",
    "ParamStabilityError",
    "Point",
    "UniverseSnapshot",
    "assert_no_overlap",
    "deflated_sharpe",
    "make_splits",
    "pbo_cscv",
    "require_cost_model",
    "stability_score",
]
