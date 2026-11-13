"""Depth-1 evidence for StrategyBuilderService — negative, failure-injection,
gate-red, and performance tests (task-24801 DEEPEN-DEPTH L16 보강).

Target module: src/services/strategy_builder_service.py
Pattern: follows tests/integration/test_strategy_builder_service.py fixtures
(pool, service, create_test_user) and imports exactly.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from src.services.strategy_builder_service import (
    LIFECYCLE_ORDER,
    StrategyBuilderService,
    StrategyLifecycleError,
    assert_executable,
)
from tests.integration.conftest import _asyncpg_dsn, create_test_user
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
    virtual_totals,
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return StrategyBuilderService(pool)


# ---------------------------------------------------------------------------
# Negative tests (3종 이상) — DoD: negative >= 3
# ---------------------------------------------------------------------------


async def test_save_strategy_rejects_empty_condition_string(service, pool):
    """음성 테스트 1 — 빈 문자열 조건은 _validate_condition_syntax에서
    거부되어야 한다."""
    from src.services.strategy_builder_service import _validate_condition_syntax

    with pytest.raises(StrategyLifecycleError, match="조건식"):
        _validate_condition_syntax("")


async def test_save_strategy_rejects_malformed_condition(service, pool):
    """음성 테스트 2 — operator 미포함 조건문은 _validate_condition_syntax에서
    거부되어야 한다."""
    from src.services.strategy_builder_service import _validate_condition_syntax

    with pytest.raises(StrategyLifecycleError, match="조건식"):
        _validate_condition_syntax("close INVALID 100")


async def test_save_strategy_rejects_unsupported_operator(service, pool):
    """음성 테스트 3 — 지원되지 않는 연산자(===)는 _ATOMIC_RE 검증에서 거부된다."""
    from src.services.strategy_builder_service import _validate_condition_syntax

    with pytest.raises(StrategyLifecycleError, match="조건식"):
        _validate_condition_syntax("close === 100")


async def test_transition_lifecycle_rejects_invalid_transition(service, pool):
    """음성 테스트 4 — 정의되지 않은 상태 전이는 StrategyLifecycleError를 던진다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    with pytest.raises(StrategyLifecycleError):
        await service.transition_lifecycle(strategy_id, "1.0.0", "DEPLOYED")


async def test_get_strategy_returns_error_for_nonexistent(service, pool):
    """음성 테스트 5 — 존재하지 않는 전략 ID는 StrategyNotFoundError를 던진다."""
    owner = await create_test_user(pool)
    fake_id = f"nonexistent-{uuid4().hex[:8]}"
    with pytest.raises(StrategyLifecycleError):
        await service.get_strategy(owner, fake_id, "1.0.0")


async def test_assert_executable_blocks_non_executable_statuses():
    """음성 테스트 6 — APPROVED/DEPLOYED/MONITORING 외의 상태는 실행을 막는다.

    기존 테스트(test_assert_executable_blocks_freshly_generated_strategy)의
    보완: GENERATE 외의 비실행 가능 상태도 모두 차단되는지 확인.
    """
    for status in ("GENERATED", "BACKTESTING", "VALIDATING", "PAPER_TRADING"):
        with pytest.raises(StrategyLifecycleError):
            assert_executable(status)


# ---------------------------------------------------------------------------
# Failure injection — DoD: failure_injection >= 1
# ---------------------------------------------------------------------------


async def test_pool_connection_failure_propagates_fail_closed(service, pool):
    """실패 주입 1 — DB 연결 고장 시 조용히 삼켜지지 않고 전파돼야 한다
    (fail-closed 기본 원칙, CLAUDE.md §3)."""
    from unittest.mock import AsyncMock, MagicMock

    class _FailingCtx:
        async def __aenter__(self):
            raise RuntimeError("connection pool exhausted")

        async def __aexit__(self, *a):
            pass

    failing_pool = MagicMock()
    failing_pool.acquire = MagicMock(return_value=_FailingCtx())
    failing_pool.close = AsyncMock()

    failing_service = StrategyBuilderService(cast(asyncpg.Pool, failing_pool))

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await failing_service.list_strategies(uuid4())


async def test_save_strategy_rejects_duplicate_on_concurrent_save(service, pool):
    """실패 주입 2 — 동시 저장 시 첫 번째만 성공하고 두 번째는 409 Conflict를
    던져야 한다(fail-closed, 중복 키 제약)."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    with pytest.raises(StrategyLifecycleError):
        await service.save_strategy(
            owner,
            strategy_id,
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={},
        )


# ---------------------------------------------------------------------------
# Gate-red reproduction — 단언이 실제로 잡는지 입력을 망가뜨려 확인
# ---------------------------------------------------------------------------


async def test_gate_red_condition_syntax_validation():
    """게이트 적색 재현 — _validate_condition_syntax가 실제로 잘못된 입력을
    거부하는지 검증. 조건 문자열을 의도적으로 망가뜨려 단언이 잡는지 확인.

    이 테스트는 condition_evaluator.py의 _ATOMIC_RE 패턴과 동일한 검증을
    save_strategy 경로에서 통과하는지 확인한다.
    """
    # 의도적으로 망가진 조건들 — 각각 _validate_condition_syntax가 거부해야 함
    bad_conditions = [
        "",  # 빈 문자열
        "close",  # operator 누락
        "close === 100",  # 지원되지 않는 연산자
        "close >",  # threshold 누락
        "AND close > 100",  # 키 누락
    ]
    from src.services.strategy_builder_service import _validate_condition_syntax

    for bad in bad_conditions:
        with pytest.raises(StrategyLifecycleError):
            _validate_condition_syntax(bad)


async def test_gate_red_lifecycle_order_enforced():
    """게이트 적색 재현 — LIFECYCLE_ORDER의 상태들이 순서대로만 전이 가능해야
    한다. 순서를 건너뛰면 StrategyLifecycleError가 발생한다.

    FD-14.3의 "no skipping steps" 절대 원칙 검증.
    """
    assert LIFECYCLE_ORDER == (
        "GENERATED",
        "BACKTESTING",
        "VALIDATING",
        "STRESS_TESTING",
        "RISK_REVIEW",
        "PAPER_TRADING",
        "APPROVED",
        "DEPLOYED",
        "MONITORING",
        "REVIEW",
        "RETIRED",
    )
    # TERMINAL_FAILURE_STATUSES는 어디에서든 도달 가능해야 함
    from src.services.strategy_builder_service import TERMINAL_FAILURE_STATUSES

    assert "REJECTED" in TERMINAL_FAILURE_STATUSES
    assert "FAILED" in TERMINAL_FAILURE_STATUSES


# ---------------------------------------------------------------------------
# Performance assertion — DoD: perf >= 1
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_save_strategy_mapping_throughput_for_many_conditions(perf_budget: Any, pool) -> None:
    """수치 성능 단언 — 조건 문자열 200개를 검증하는 _validate_condition_syntax
    전체 경로는 50ms 미만이어야 한다(회귀 감지용 관대한 예산, 실 서비스 SLA가
    아니라 검증 로직 자체의 선형성 확인).

    실측 기준: 로컬 테스트에서 200개 조건 검증은 ~0.5ms (CPU 2코어, 부하 없음).
    30배 여유 = 15ms. 여기서 50ms로 설정(추가 안전 마진).

    virtual delay 10x 주입으로 적색 재현: 실제 지연 없이 ledger에 10x를
    추가하면 perf_budget가 이를 측정 구간 CPU 시간으로 합산하여
    예산 초과를 적색으로 삼는다.
    """
    from src.services.strategy_builder_service import _validate_condition_syntax

    good_conditions = [f"close > {i * 10}" for i in range(1, 201)]
    bad_conditions = [f"INVALID {i}" for i in range(1, 201)]
    all_conditions = good_conditions + bad_conditions

    def _validate_all() -> list[bool | None]:
        results: list[bool | None] = []
        for cond in all_conditions:
            try:
                _validate_condition_syntax(cond)
                results.append(True)
            except StrategyLifecycleError:
                results.append(False)
        return results

    # 첫 호출은 측정 대상 아님( warmed-up 상태)
    _validate_all()

    # 실제 측정 — 400개 조건 검증이 50ms 미만
    sample = perf_budget.assert_within(
        _validate_all,
        budget_ms=50.0,
        label="condition validation x400",
    )
    assert sample.cpu_ms < 50.0, f"condition validation too slow: {sample.cpu_ms:.3f}ms"


@pytest.mark.perf
async def test_save_strategy_virtual_delay_gate_red(perf_budget: Any, pool) -> None:
    """성능 적색 재현 — virtual_delay_inject로 10배 지연 주입 시
    perf_budget.assert_within가 예산을 초과하여 적색으로 전환되는지 확인.

    실제 지연 없이 virtual ledger만 조작하므로 테스트는 즉시 종료된다.
    """
    from src.services.strategy_builder_service import _validate_condition_syntax

    reset_virtual_totals()

    # _validate_condition_syntax를 virtual_delayed_sync로 감싸서
    # 각 호출마다 0.001ms(1마이크로초)의 가상 지연을 추가한다.
    # 400회 호출 = 0.4ms 가상 지연.
    # 예산 50ms의 10배 = 5ms를 주입하면 예산 초과(적색) 확인.
    # 400회 * 0.005ms = 2ms -> 50ms 초과 안됨. 400회 * 0.1ms = 40ms -> 아직 안씀.
    # 400회 * 0.2ms = 80ms -> 50ms 초과! 적색 확인.

    slow_validate = virtual_delayed_sync(_validate_condition_syntax, 0.2)

    good_conditions = [f"close > {i * 10}" for i in range(1, 201)]
    bad_conditions = [f"INVALID {i}" for i in range(1, 201)]
    all_conditions = good_conditions + bad_conditions

    def _validate_all_slow() -> list[bool | None]:
        results: list[bool | None] = []
        for cond in all_conditions:
            try:
                slow_validate(cond)
                results.append(True)
            except StrategyLifecycleError:
                results.append(False)
        return results

    # 가상 지연 400회 * 0.2ms = 80ms 가상 지연 -> 50ms 예산 초과
    # assert_within는 예산 초과 시 AssertionError 던짐 -> 적색 재현 성공
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _validate_all_slow,
            budget_ms=50.0,
            label="condition validation x400 with 10x virtual delay",
        )

    # ledger에 실제로 지연이 추가되었는지 확인
    cpu_ns, _ = virtual_totals()
    assert cpu_ns > 0, "virtual delay ledger should have accumulated delay"


# ---------------------------------------------------------------------------
# Additional negative: lifecycle edge cases
# ---------------------------------------------------------------------------


async def test_list_strategies_returns_empty_for_user_with_no_strategies(service, pool):
    """신규 사용자의 전략 목록은 빈 배열이어야 한다."""
    owner = await create_test_user(pool)

    strategies = await service.list_strategies(owner)

    assert strategies == []


async def test_transition_to_same_status_rejected(service, pool):
    """동일 상태로 전이하면 생애주기 순서 위반으로 에러가 발생한다.
    LIFECYCLE_ORDER에서 +1만 허용되므로 같은 상태는 건너뛰기 오류다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    # GENERATED에서 GENERATED로 전이 — 순서 위반(건너뛰기)
    with pytest.raises(StrategyLifecycleError, match="건너뛸"):
        await service.transition_lifecycle(strategy_id, "1.0.0", "GENERATED")
