"""L4_strategy_portfolio_backtest_v1.0.md §9 L41 -- unit tests for
`application/compile_artifact.py`. No DB needed: `StrategyBuilderService` is
replaced with a fake duck-typed object exposing the same `get_strategy`
coroutine shape (mirrors `tests/foundation/unit/validation/checks/
test_backtest.py`'s "no DB, real domain code" style).
"""

from __future__ import annotations

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
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import delayed_async


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


@pytest.mark.perf
async def test_p95_latency_within_pure_compile_budget(perf_budget) -> None:
    """raw time.perf_counter() → perf_budget.samples_async() 전환 (task-11017).

    비동기 I/O를 재는 테스트는 RelativeBudget.measure_async / samples_async를
    쓴다 — wall_ms로 왕복 지연을 보존하고 coverage tracer 오버헤드를 걷어낸다.
    예산 값(200ms)은 그대로 유지한다.
    """
    fsm = {"states": [f"S{i}" for i in range(50)]}
    service = _FakeStrategyService(fsm)
    n = 100

    async def _fn() -> None:
        await compile_artifact(
            service, owner_user_id=uuid4(), strategy_id="strat-1", strategy_version="v1"
        )

    samples = await perf_budget.samples_async(_fn, n=n)
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(n * 0.95), len(wall_ms_list) - 1)]
    assert p95_ms < 200, f"p95={p95_ms:.2f}ms exceeds 200ms budget"


# -- gate-red: delay injection proves the budget assertion is non-trivial --------


@pytest.mark.perf
async def test_gate_red_injected_delay_exceeds_pure_compile_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_strategy 에 0.5s 지연(delayed_async) 을 주입하면 측정 값이
    성능 예산을 초과한다(red).

    정상 입력에서 통과하는 것이 테스트의 허수(trivial pass)가 아님을
    보증한다 — 지연 주입 후 wall_ms 가 200ms 예산을 넘으면 단언이
    실제로 실행 시간을 측정함을 증명한다.
    """
    fsm = {"states": [f"S{i}" for i in range(50)]}
    service = _FakeStrategyService(fsm)

    # 0.5s = 500ms 지연 주입 — get_strategy 의 async 호출을 느리게 만든다
    slow_get_strategy = delayed_async(service.get_strategy, 0.5)
    monkeypatch.setattr(service, "get_strategy", slow_get_strategy)

    async def _fn() -> None:
        await compile_artifact(
            service, owner_user_id=uuid4(), strategy_id="strat-1", strategy_version="v1"
        )

    sample = await perf_budget.sample_async(_fn, batch=1)
    assert sample.wall_ms >= 200, (
        f"지연 주입 후 wall 시간 {sample.wall_ms:.3f}ms — "
        f"예상과 달리 녹색: 측정/단언이 공허함(hollow assertion, "
        f"budget=200ms)"
    )


# -- zero CPU convergence --------------------------------------------------------


@pytest.mark.perf
async def test_single_call_cpu_converges_to_zero(perf_budget: PerfBudget) -> None:
    """단일 호출의 CPU 시간이 0 수렴함을 동적으로 검증한다.

    `compile_artifact` 는 FSM 정의 해싱만 수행 — 실제 I/O 가 없다.
    CPU 예산 5ms 미만에서 best-of-5 최소값이 수렴해야 한다.
    """
    fsm = {"states": ["IDLE", "BUY_ORDER_PENDING"]}
    service = _FakeStrategyService(fsm)

    async def _fn() -> None:
        await compile_artifact(
            service, owner_user_id=uuid4(), strategy_id="strat-1", strategy_version="v1"
        )

    sample = await perf_budget.sample_async(_fn, batch=1)
    assert sample.cpu_ms < 5, (
        f"single-call CPU={sample.cpu_ms:.3f}ms exceeds 5ms budget — "
        f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
    )
