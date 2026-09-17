"""L4_strategy_portfolio_backtest_v1.0.md §9 L41 -- unit tests for
`application/compile_artifact.py`. No DB needed: `StrategyBuilderService` is
replaced with a fake duck-typed object exposing the same `get_strategy`
coroutine shape (mirrors `tests/foundation/unit/validation/checks/
test_backtest.py`'s "no DB, real domain code" style).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.validation.application.compile_artifact import (
    COMPILER_VERSION,
    StrategyNotFoundForCompilationError,
    compile_artifact,
)
from src.services.strategy_builder_service import StrategyLifecycleError, StrategyNotFoundError


@dataclass
class _FakeDetail:
    fsm_definition: dict[str, Any]


class _FakeStrategyService:
    def __init__(self, fsm_definition: dict[str, Any] | None = None, *, raises: bool = False):
        self._fsm_definition = fsm_definition or {"states": ["IDLE"]}
        self._raises = raises

    async def get_strategy(self, owner_user_id, strategy_id, strategy_version):
        if self._raises:
            raise StrategyNotFoundError("존재하지 않거나 접근 권한이 없는 전략입니다.")
        return _FakeDetail(fsm_definition=self._fsm_definition)


async def test_compiles_content_addressed_artifact_from_current_fsm_shape() -> None:
    service = _FakeStrategyService({"states": ["IDLE", "BUY_ORDER_PENDING"]})
    artifact = await compile_artifact(
        service, owner_user_id=uuid4(), strategy_id="strat-1", strategy_version="v1"
    )
    assert artifact.strategy_id == "strat-1"
    assert artifact.version == "v1"
    assert artifact.compiler_version == COMPILER_VERSION
    assert artifact.artifact_hash


async def test_same_fsm_shape_compiles_to_the_same_hash() -> None:
    fsm = {"states": ["IDLE", "BUY_ORDER_PENDING"]}
    first = await compile_artifact(
        _FakeStrategyService(fsm),
        owner_user_id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
    )
    second = await compile_artifact(
        _FakeStrategyService(fsm),
        owner_user_id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
    )
    assert first.artifact_hash == second.artifact_hash


# -- negative --------------------------------------------------------------------


async def test_different_fsm_shape_compiles_to_a_different_hash() -> None:
    same_strategy_kwargs = dict(owner_user_id=uuid4(), strategy_id="strat-1", strategy_version="v1")
    a = await compile_artifact(_FakeStrategyService({"states": ["IDLE"]}), **same_strategy_kwargs)
    b = await compile_artifact(
        _FakeStrategyService({"states": ["IDLE", "HOLDING"]}), **same_strategy_kwargs
    )
    assert a.artifact_hash != b.artifact_hash


async def test_unknown_or_unauthorized_strategy_raises() -> None:
    service = _FakeStrategyService(raises=True)
    with pytest.raises(StrategyNotFoundForCompilationError):
        await compile_artifact(
            service, owner_user_id=uuid4(), strategy_id="ghost", strategy_version="v1"
        )


async def test_underlying_lifecycle_error_type_is_not_leaked_unwrapped() -> None:
    """A caller catching `StrategyNotFoundForCompilationError` must not also
    need to catch the raw `StrategyLifecycleError` -- confirms the except
    clause actually re-raises a new type instead of merely re-raising the
    original unchanged."""
    service = _FakeStrategyService(raises=True)
    try:
        await compile_artifact(
            service, owner_user_id=uuid4(), strategy_id="ghost", strategy_version="v1"
        )
    except StrategyNotFoundForCompilationError as exc:
        assert not isinstance(exc, StrategyLifecycleError)
    else:
        pytest.fail("expected StrategyNotFoundForCompilationError")


# -- D2 numeric performance assertion ----------------------------------------------


async def test_p95_latency_within_pure_compile_budget() -> None:
    """Compiling is pure (no I/O beyond the injected fake read) -- 100
    sequential compiles of a moderately-sized FSM definition must stay well
    under a 200ms floor."""
    fsm = {"states": [f"S{i}" for i in range(50)]}
    service = _FakeStrategyService(fsm)
    samples: list[float] = []
    for _ in range(100):
        start = time.perf_counter()
        await compile_artifact(
            service, owner_user_id=uuid4(), strategy_id="strat-1", strategy_version="v1"
        )
        samples.append(time.perf_counter() - start)
    p95_seconds = sorted(samples)[int(len(samples) * 0.95)]
    assert p95_seconds < 0.2, f"p95={p95_seconds * 1000:.2f}ms exceeds 200ms budget"
