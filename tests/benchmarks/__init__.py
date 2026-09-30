"""PerfBudget(tests/conftest.py) DEEPEN — negative/실패주입 보강.

task-10084 (원 리프 task-6704 "고아 산출물 회수 5828 qa-2") — 이 패키지의
`__init__.py`는 처음부터 빈 파일이었다(회수 대상 files=[]에 이것만 지정됨).
이 패키지의 유일한 테스트(`test_backtest_throughput.py`)는 `run_backtest()`
자체의 처리량만 재고, 모든 벤치마크가 공유하는 측정 하네스인
`tests.conftest.PerfBudget`은 어디에도 직접 단위테스트가 없었다 — 그
간극을 여기서 메운다(`tests/foundation/unit/performance/__init__.py`
DEEPEN(task-8406)과 동일한 원칙: 패키지가 실제로 의존하는, 아직 D2가 없는
가장 가까운 도메인 대상을 고른다).

Spec: docs/design/INVARIANTS.md — 성능 예산 판정은 fail-closed여야 하고
(예산 초과를 조용히 통과시키지 않음), 의존성(psutil) 실패를 "정보 없음"으로
삼켜 잘못된 성공을 내면 안 된다."""

from __future__ import annotations

import sys

import psutil
import pytest

from tests.conftest import PerfBudget

# ---------------------------------------------------------------------------
# Negative test 1: assert_within은 측정된 CPU 시간이 예산을 넘으면 반드시
# AssertionError를 던진다 — 이 벤치마크 하네스 자체가 tautology라 언제나
# 통과한다면 이 패키지의 다른 모든 성능단언이 무의미해진다.
# ---------------------------------------------------------------------------


def test_assert_within_raises_when_measured_cpu_exceeds_budget() -> None:
    def _busy() -> int:
        total = 0
        for i in range(2_000_000):
            total += i
        return total

    with pytest.raises(AssertionError):
        PerfBudget().assert_within(_busy, budget_ms=0.001, n=1, warmup=0)


# ---------------------------------------------------------------------------
# Negative test 2: sample()은 측정 대상 함수가 던진 예외를 삼켜 "측정 실패"를
# 조용한 결과값으로 둔갑시키지 않고 그대로 전파해야 하며, `sys.settrace()`를
# 임시로 끈 뒤 finally에서 반드시 원래 트레이서로 복원해야 한다(예외 경로에서
# 복원이 빠지면 이후 커버리지 계측 전체가 깨진다).
# ---------------------------------------------------------------------------


def test_sample_propagates_fn_exception_and_restores_trace() -> None:
    failure = RuntimeError("injected fn failure")

    def _broken() -> None:
        raise failure

    original_trace = sys.gettrace()
    with pytest.raises(RuntimeError, match="injected fn failure") as excinfo:
        PerfBudget().sample(_broken)
    assert excinfo.value is failure
    assert sys.gettrace() == original_trace


# ---------------------------------------------------------------------------
# Negative test 3: best_of()는 warmup 호출에서 예외가 나면 즉시 전파해야
# 한다 — warmup 실패를 삼키고 본측정으로 넘어가면 "측정 안 된 채 통과"라는
# 가장 위험한 형태의 가짜 성공이 된다.
# ---------------------------------------------------------------------------


def test_best_of_propagates_exception_raised_during_warmup() -> None:
    calls: list[int] = []

    def _flaky() -> None:
        calls.append(1)
        raise ValueError("boom during warmup")

    with pytest.raises(ValueError, match="boom during warmup"):
        PerfBudget().best_of(_flaky, n=3, warmup=1)
    assert calls == [1], f"warmup 실패 후 추가 호출이 있었다 — calls={calls}"


# ---------------------------------------------------------------------------
# 실패주입: load_percent()가 의존하는 psutil.cpu_percent()가 예외를 던지면
# "부하 정보 없음"(None)으로 조용히 대체하지 않고 그대로 전파해야 한다 —
# None으로 삼키면 describe()가 진짜 부하 상황(코어 경합으로 인한 실측치
# 왜곡)과 psutil 장애를 구분할 수 없게 된다.
# ---------------------------------------------------------------------------


def test_load_percent_does_not_swallow_psutil_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_cpu_percent(interval: float | None = None) -> float:
        raise RuntimeError("injected psutil failure")

    monkeypatch.setattr(psutil, "cpu_percent", _broken_cpu_percent)

    with pytest.raises(RuntimeError, match="injected psutil failure"):
        PerfBudget().load_percent()
