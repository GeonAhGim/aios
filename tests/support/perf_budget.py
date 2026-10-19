"""task-6774 공용 perf 측정 헬퍼 — conftest.py에서 분리(ADR-2026-09-10-C §7,
tests/conftest.py가 500줄 임계값을 넘겨 분할, task-12088)."""

from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from tests.support.coverage_pause import paused_coverage

try:
    import psutil
except ImportError:  # pragma: no cover -- psutil is not a declared hard
    # dependency (it happens to be present in some dev venvs as a transitive
    # tool dependency); load reporting degrades to "n/a" without it instead
    # of failing perf tests over a missing optional import.
    psutil = None

_T = TypeVar("_T")


@dataclass(frozen=True)
class PerfSample:
    """단일 1회 호출의 측정값. `cpu_ms`가 예산 판정 기준이고, `wall_ms`는
    실패 메시지에서 "이 노력이 얼마나 다른 프로세스에 눌렸는지"를 보여주는
    참고값이다(`cpu_ms` << `wall_ms`면 이 워커가 코어를 뺏겼다는 뜻)."""

    cpu_ms: float
    wall_ms: float
    result: object


class PerfBudget:
    """task-6774 — `perf` 마커(또는 budget/p95/latency 단언) 테스트가 예산
    측정 방식을 파일마다 재구현하지 않도록 공용화한 헬퍼. 측정은 항상
    `time.process_time()`(이 프로세스가 실제로 소비한 CPU 시간)을 기준으로
    한다 — `time.perf_counter()` wall-clock과 달리, 다른 워커 프로세스나 이
    호스트에서 함께 도는 llama.cpp 추론에 코어를 뺏겨 대기한 시간이 계측에
    섞이지 않는다(task-6371이 corporate_actions 테스트 한 곳에만 적용했던
    수법을 모든 perf 테스트로 일반화한 것). `best_of`는 그 위에 best-of-N
    최소값을 더해 노이즈를 한 번 더 걷어낸다; `samples`는 p95/p99처럼
    분포 자체가 필요한 테스트를 위해 원시 샘플 목록을 돌려준다."""

    def sample(self, fn: Callable[[], _T], *, batch: int = 1) -> PerfSample:
        """`batch`>1이면 `fn`을 연속 `batch`회 호출한 총 시간을 `batch`로
        나눠 1회 호출당 시간을 추정한다. `time.process_time()`은 Windows에서
        약 15.6ms(64Hz) 해상도로 양자화된다(`GetProcessTimes` 클록 틱) —
        측정 대상이 그보다 훨씬 빠르면 매 호출이 0ms 또는 15.625ms 중
        하나로만 읽혀 p95/평균이 왜곡된다. `batch`로 여러 호출을 한 구간에
        묶으면 총 CPU 시간이 그 틱 폭보다 커져 양자화 오차가 호출당
        `tick/batch`로 줄어든다(예: batch=8이면 오차가 ~2ms로 줄어든다).

        task-7253(esc-ci-pytest_perf)이 이미 이 메서드에서 coverage
        line-tracer 오버헤드를 측정 구간 밖으로 빼 뒀다(아래 주석). 같은
        근본 원인(esc-ci-coverage)이 `PerfBudget`을 거치지 않고 raw
        `time.perf_counter()`를 직접 쓰는 테스트에도 있어, 그쪽은
        `paused_coverage()`로 동일하게 고쳤다 — task-7250."""
        result: _T | None = None
        # task-7253(esc-ci-pytest_perf): the `pytest_perf` CI step runs with
        # `--cov=src --cov-append` for coverage accounting. coverage.py's line
        # tracer hooks via `sys.settrace()` and adds real per-line CPU work in
        # *this* process, so `time.process_time()` captures tracer overhead
        # alongside the code under test -- a tight inner loop (e.g. a few
        # hundred thousand comparisons) can see its measured cpu_ms multiply
        # under coverage even though the code itself did not regress. Suspend
        # tracing for the timed section only; coverage of the surrounding test
        # body (not the hot loop) is unaffected since it is retraced right
        # after restore.
        active_tracer = sys.gettrace()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        sys.settrace(None)
        try:
            for _ in range(batch):
                result = fn()
        finally:
            sys.settrace(active_tracer)
        cpu_ms = (time.process_time() - cpu_start) * 1000 / batch
        wall_ms = (time.perf_counter() - wall_start) * 1000 / batch
        return PerfSample(cpu_ms=cpu_ms, wall_ms=wall_ms, result=result)

    async def sample_async(self, fn: Callable[[], Awaitable[_T]], *, batch: int = 1) -> PerfSample:
        """비동기 I/O는 커버리지 계측 없이 wall_ms로 왕복 지연을 보존한다.

        `sys.settrace` 일시 정지도 함께 적용 — pytest --cov 가 건넨 line-tracer
        는 매 coroutine switch 마다 CPU를 먹으므로(동기 `sample`의 처리와 같은
        근본 원인), async 구간에서도 동일한 패턴으로 제거해야 CI 병렬 실행에서
        budget 단언이 안정적으로 통과한다(task-15234).

        `batch`>1이면 `fn`을 연속 `batch`회 호출한 평균을 반환 — Windows의
        `time.process_time()` 약 15.6ms 양자화 오차를 `tick/batch`로 줄인다.
        """
        active_tracer = sys.gettrace()
        with paused_coverage():
            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            sys.settrace(None)
            try:
                for _ in range(batch):
                    result = await fn()
            finally:
                sys.settrace(active_tracer)
            return PerfSample(
                cpu_ms=(time.process_time() - cpu_start) * 1000 / batch,
                wall_ms=(time.perf_counter() - wall_start) * 1000 / batch,
                result=result,
            )

    async def samples_async(self, fn: Callable[[], Awaitable[_T]], *, n: int) -> list[PerfSample]:
        """비동기 I/O n회 반복 측정 — coverage tracer 일시정지 + 원시 샘플 목록 반환."""
        samples: list[PerfSample] = []
        for _ in range(n):
            samples.append(await self.sample_async(fn))
        return samples

    def samples(
        self, fn: Callable[[], _T], *, n: int, warmup: int = 1, batch: int = 1
    ) -> list[PerfSample]:
        for _ in range(warmup):
            fn()
        return [self.sample(fn, batch=batch) for _ in range(n)]

    def best_of(
        self, fn: Callable[[], _T], *, n: int = 5, warmup: int = 1, batch: int = 1
    ) -> PerfSample:
        candidates = self.samples(fn, n=n, warmup=warmup, batch=batch)
        return min(candidates, key=lambda s: s.cpu_ms)

    def load_percent(self) -> float | None:
        """`os.getloadavg()`는 Windows에 없다 — 이 리포는 Windows 호스트에서도
        돈다(CLAUDE.md 환경 섹션). `psutil.cpu_percent`가 설치돼 있으면 그것을
        쓰고, 없으면 실패 메시지에 부하 정보 없이(`None`) 진행한다(선택적
        의존성, §섹션 상단 import 주석 참조)."""
        if psutil is None:
            return None
        return float(psutil.cpu_percent(interval=None))

    def describe(self, sample: PerfSample, *, budget_ms: float) -> str:
        load = self.load_percent()
        load_str = f"{load:.0f}%" if load is not None else "n/a"
        return (
            f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms "
            f"load={load_str} budget<{budget_ms:.3f}ms"
        )

    def assert_within(
        self,
        fn: Callable[[], _T],
        *,
        budget_ms: float,
        n: int = 5,
        warmup: int = 1,
        batch: int = 1,
        label: str = "",
    ) -> PerfSample:
        sample = self.best_of(fn, n=n, warmup=warmup, batch=batch)
        prefix = f"{label}: " if label else ""
        assert sample.cpu_ms < budget_ms, prefix + self.describe(sample, budget_ms=budget_ms)
        return sample
