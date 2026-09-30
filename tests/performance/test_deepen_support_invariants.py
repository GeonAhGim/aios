"""DEEPEN(task-9847, 원 리프 task-6704 고아 산출물 회수 5828 qa-2) —
`tests/performance/__init__.py` negative/실패주입 보강.

task-8658(327b0d137) 실측 선례대로 `tests/performance/__init__.py` 자체에
`test_*` 함수를 추가하지 않는다 — 이 저장소 pytest 설정(`[tool.pytest.ini_options]`,
`python_files` 오버라이드 없음 = 기본값 `test_*.py`)은 `__init__.py`를 test
모듈로 수집하지 않으므로 그 안의 assert는 한 번도 실행되지 않는 dead code가
된다. 대신 그 DEEPEN 대상이 가리키는 실질 결함(`pre_trade_latency_support.py`의
`percentile()`이 단위 테스트 없이 `test_pre_trade_latency.py`에서만 간접 사용되고,
`count_pre_trade_round_trips`의 "outcome != ALLOW면 조용히 잘못된 왕복수를
반환하지 않고 fail-closed로 죽는다" 불변식이 실 DB 없이는 검증된 적 없다)를
이 파일에서 채운다. DB 불필요 — `count_pre_trade_round_trips`는 `scenario.run_once`
결과만으로 분기하므로 `PreTradeScenario`/실 커넥션을 전부 대역으로 바꾼다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.risk.decision import RiskOutcome
from tests.performance.pre_trade_latency_support import (
    count_pre_trade_round_trips,
    percentile,
)

# ---------------------------------------------------------------------------
# percentile() — 순수 함수, I/O 없음
# ---------------------------------------------------------------------------


def test_percentile_empty_samples_raises_index_error() -> None:
    """빈 샘플의 백분위는 정의되지 않는다 — 조용히 0.0을 반환하면 p99 예산
    단언이 항상 통과하는 거짓양성 게이트가 된다(불변식 위반 입력 거부)."""
    with pytest.raises(IndexError):
        percentile([], 99)


def test_percentile_single_sample_returns_that_value() -> None:
    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 99) == 42.0


def test_percentile_pct_zero_returns_minimum() -> None:
    assert percentile([5.0, 1.0, 3.0], 0) == 1.0


def test_percentile_pct_over_100_clamped_to_maximum() -> None:
    """pct > 100은 불변식 위반 입력이지만 fail-closed로 예외를 던지는 대신
    nearest-rank 최댓값으로 클램프된다(round()가 index 상한을 넘겨도
    min(len-1, ...)이 잡는다) — 이 클램프 동작 자체를 명시적으로 고정한다."""
    assert percentile([1.0, 2.0, 3.0], 150) == 3.0


def test_percentile_negative_pct_clamped_to_minimum() -> None:
    assert percentile([1.0, 2.0, 3.0], -50) == 1.0


@pytest.mark.perf
def test_percentile_p99_over_10k_samples_within_budget(perf_budget) -> None:
    """성능단언(D2): 1만 포인트 규모에서도 nearest-rank 계산은 sort 1회
    (O(n log n))로 끝나야 한다 — 공유 CI 편차를 감안해 500ms 상한(여유
    수십 배)만 건다."""
    samples = [float(i % 997) for i in range(10_000)]
    measured = perf_budget.assert_within(
        lambda: percentile(samples, 99), budget_ms=500, n=1, warmup=0
    )
    assert measured.result == 986.0


@pytest.mark.perf
def test_percentile_perf_assertion_actually_catches_regression(perf_budget) -> None:
    """적색 재현(tautology 방지): 위 성능 단언이 실제로 실패할 수 있음을
    sleep 지연 주입으로 확인한다 — 항상 통과하는 장식 단언이 아니다."""
    import time

    def _slow_percentile(samples_ms: list[float], pct: float) -> float:
        time.sleep(0.6)  # 500ms 예산을 의도적으로 초과시키는 실패 주입
        return percentile(samples_ms, pct)

    measured = perf_budget.sample(lambda: _slow_percentile([1.0, 2.0, 3.0], 99))
    with pytest.raises(AssertionError):
        assert measured.wall_ms < 500, f"took {measured.wall_ms:.1f}ms (budget 500ms)"


# ---------------------------------------------------------------------------
# count_pre_trade_round_trips — fail-closed 불변식(outcome != ALLOW), 실 DB 없이
# 전부 대역으로 검증
# ---------------------------------------------------------------------------


class _FakeConn:
    def add_query_logger(self, log: Any) -> None:
        pass

    def remove_query_logger(self, log: Any) -> None:
        pass


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class _FakeScenario:
    """`PreTradeScenario.run_once`와 같은 시그니처만 흉내 낸다 — 실 캐시/
    adapter/risk_engine 없이 outcome을 직접 주입한다."""

    def __init__(self, outcome: object | None) -> None:
        self._outcome = outcome

    async def run_once(self, pool: object, recorder: object) -> object | None:
        return self._outcome


def _fake_outcome(outcome: RiskOutcome) -> SimpleNamespace:
    return SimpleNamespace(decision=SimpleNamespace(outcome=outcome))


@pytest.mark.asyncio
async def test_count_pre_trade_round_trips_raises_when_outcome_is_none() -> None:
    """`run_pre_trade_risk_phase`가 무엇이든 이유로 None을 반환하면(REDUCE
    축소 등 조기 반환 경로) 계수 헬퍼는 그 값을 왕복수 0으로 조용히 흘려보내지
    않고 즉시 죽어야 한다(fail-closed, I-10과 동일 원칙)."""
    pool = _FakePool(_FakeConn())
    scenario = _FakeScenario(None)
    with pytest.raises(AssertionError):
        await count_pre_trade_round_trips(pool, scenario)


@pytest.mark.asyncio
async def test_count_pre_trade_round_trips_raises_when_outcome_is_deny() -> None:
    """워밍업 호출이 DENY로 나오면(고정 시나리오가 깨졌다는 신호) 계수
    헬퍼는 그 잘못된 상태로 측정을 계속하지 않고 전파한다 — 실패주입(D2)."""
    pool = _FakePool(_FakeConn())
    scenario = _FakeScenario(_fake_outcome(RiskOutcome.DENY))
    with pytest.raises(AssertionError):
        await count_pre_trade_round_trips(pool, scenario)


@pytest.mark.asyncio
async def test_count_pre_trade_round_trips_succeeds_on_allow() -> None:
    """양성 대조군 — 두 위 테스트가 ALLOW 자체를 우연히 거부하는 게 아님을
    증명한다(tautology 방지)."""
    pool = _FakePool(_FakeConn())
    scenario = _FakeScenario(_fake_outcome(RiskOutcome.ALLOW))
    result = await count_pre_trade_round_trips(pool, scenario)
    assert result == 0  # FakeConn에 쿼리 로거가 실제 쿼리를 남기지 않음
