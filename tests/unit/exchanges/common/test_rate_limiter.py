"""L4-11 — rate_limiter 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11

가짜 clock/sleep을 주입해 실제 대기 없이 결정론적으로 검증한다(sleep 금지
규칙 — task-423 d3227c9 패턴).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from time import perf_counter

import pytest

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.rate_limiter import TokenBucket


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _make_bucket(rate: float, burst: float) -> tuple[TokenBucket, _FakeClock, list[float]]:
    clock = _FakeClock()
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.now += seconds

    bucket = TokenBucket(rate, burst, clock=clock, sleep=fake_sleep)
    return bucket, clock, sleep_calls


async def test_acquire_within_burst_does_not_wait() -> None:
    bucket, _clock, sleep_calls = _make_bucket(rate=10, burst=5)
    await bucket.acquire(3, timeout=1.0)
    assert sleep_calls == []


async def test_acquire_waits_for_refill_when_bucket_empty() -> None:
    bucket, clock, sleep_calls = _make_bucket(rate=10, burst=5)
    await bucket.acquire(5, timeout=5.0)  # 버킷 소진
    await bucket.acquire(5, timeout=5.0)  # 리필 대기 필요
    assert sleep_calls == [pytest.approx(0.5)]
    assert clock.now == pytest.approx(0.5)


async def test_acquire_raises_rate_limited_when_wait_exceeds_timeout() -> None:
    """초과 시 ExchangeError(RATE_LIMITED, retryable=True) — negative test."""
    bucket, _clock, sleep_calls = _make_bucket(rate=1, burst=1)
    await bucket.acquire(1, timeout=1.0)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(1, timeout=0.1)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert exc_info.value.retryable is True
    assert sleep_calls == []  # fail-fast: 실제로 기다리지 않고 즉시 실패


async def test_acquire_more_than_burst_rejected_immediately() -> None:
    """burst를 초과하는 토큰 요청은 즉시 RATE_LIMITED — negative 테스트 1:
    burst+1 (6) 요청 시 sleep 호출 없이 즉시 실패."""
    bucket, _clock, sleep_calls = _make_bucket(rate=10, burst=5)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(6, timeout=100.0)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []


async def test_acquire_exactly_twice_burst_rejected_immediately() -> None:
    """negative 테스트 2: 2×burst 요청도 즉시 실패 — timeout이 커도 리필로
    해결되지 않는 영역."""
    bucket, _clock, sleep_calls = _make_bucket(rate=100, burst=5)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(10, timeout=100.0)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []


async def test_acquire_hundred_times_burst_rejected_immediately() -> None:
    """negative 테스트 3: 극단적 초과(100×burst)도 즉시 실패 — timeout=0과
    동일하게 fail-fast 한다."""
    bucket, _clock, sleep_calls = _make_bucket(rate=100_000, burst=1)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(100, timeout=0)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []


async def test_acquire_more_than_burst_failure_injection_negative_clock() -> None:
    """실패 주입 D2: clock가 역주행(regrssion)해도 burst 초과 경로는 즉시
    실패해야 함 — _refill()가 elapsed<0으로 토큰을 줄이지 않는지 검증."""
    clock = _FakeClock()
    clock.now = 10.0
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.now += seconds

    bucket = TokenBucket(rate_per_sec=10, burst=5, clock=clock, sleep=fake_sleep)
    # clock를 역주행시킴 (실제 시스템에서 발생할 수 있는 clock regression)
    clock.now = 5.0
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(6, timeout=100.0)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []  # _refill()도 호출되지 않아야 함 (burst check가 먼저)


@pytest.mark.perf
async def test_acquire_more_than_burst_fast_path_latency() -> None:
    """성능 단언 D2: burst 초과 경로는 O(1) fast path — _refill()도 호출하지
    않고 즉시 raise해야 함. 10만 호출의 p95가 5ms 미만이어야 한다."""
    bucket, clock, _sleep_calls = _make_bucket(rate=10, burst=5)
    started = perf_counter()
    for _ in range(100_000):
        clock.now += 1.0  # 매 호출마다 clock 전진 (dummy)
        with pytest.raises(ExchangeError):
            await bucket.acquire(6, timeout=100.0)
    elapsed = perf_counter() - started
    assert elapsed < 5.0, (
        f"burst-exceed fast path: 100k calls took {elapsed:.3f}s "
        f"(budget 5.0s, O(1) fast path expected)"
    )


def test_acquire_more_than_burst_gate_red_reproduction(tmp_path: Path) -> None:
    """게이트 적색 재현 D2: burst 초과 검사(if n > self._burst:)를 제거하면
    (burst+1) 요청이 예외 없이 통과하므로 pytest가 적색(exit 1)이 됨 —
    단언이 실제로 잡는지 확인한다.

    기존 timeout gate red와 동일한 패턴: baseline 확인 → conftest.py로
    rate_limiter.py 모듈 객체만 변조 → 다시 실행 시 테스트가 실패함을 증명."""
    test_copy = tmp_path / "test_burst_gate_red.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        str(config),
        "--confcutdir",
        str(tmp_path),
        f"{test_copy}::test_acquire_more_than_burst_rejected_immediately",
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")
    # baseline: 정상 소스에서는 테스트 통과
    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    # rate_limiter.py 모듈 객체만 변조: burst 초과 검사 무력화
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.exchanges.common.rate_limiter'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = '            if n > self._burst:'\n"
        "assert source.count(guard) == 1\n"
        "mutated_src = source.replace(guard, '            if False:')\n"
        "mutant = compile(mutated_src, module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    # burst check가 제거되면 acquire(6)가 예외 없이 통과 → 테스트 실패
    assert mutated.returncode == 1, (
        f"gate_red: expected pytest exit 1 (test should fail when burst check is removed), "
        f"got {mutated.returncode}: {mutated.stdout}"
    )
    assert "1 failed" in mutated.stdout


@pytest.mark.perf
async def test_acquire_within_burst_latency_budget() -> None:
    """버스트 내 `acquire`는 대기 없이 반환되는 hot path — 로컬 회귀 예산이며
    SLO 단언은 아니다(headless worker 지침)."""
    bucket, clock, _sleep_calls = _make_bucket(rate=1_000_000, burst=1_000_000)
    await bucket.acquire(1, timeout=1.0)  # 콜드 스타트 워밍업 — 예산 밖.
    started = perf_counter()
    for _ in range(5_000):
        clock.now += 1.0  # 매 호출마다 충분히 리필되도록 시계를 전진시킨다.
        await bucket.acquire(1, timeout=1.0)
    elapsed = perf_counter() - started
    assert elapsed < 5.0, f"5000 within-burst acquire() calls took {elapsed:.3f}s (budget 5.0s)"


async def test_acquire_within_burst_negative_zero_tokens() -> None:
    """negative 테스트 1: acquire(0)는 0 토큰 요청 — 버퍼가 비어 있어도
    리필 없이 즉시 성공하고 대기가 없다."""
    bucket, _clock, sleep_calls = _make_bucket(rate=1, burst=1)
    await bucket.acquire(1, timeout=0.1)  # 버퍼 소진
    await bucket.acquire(0, timeout=1.0)  # 0 토큰 요청은 리필 없이 즉시 성공
    assert sleep_calls == []


async def test_acquire_within_burst_negative_large_burst() -> None:
    """negative 테스트 2: burst가 매우 크더라도 동일 — 10만 burst에서
    10만 토큰을 즉시 확보하고 대기가 없다."""
    bucket, _clock, sleep_calls = _make_bucket(rate=1_000_000, burst=100_000)
    await bucket.acquire(100_000, timeout=1.0)
    assert sleep_calls == []


async def test_acquire_within_burst_negative_zero_timeout_on_full() -> None:
    """negative 테스트 3: timeout=0이고 버퍼가 가득 차 있어도 즉시 성공 —
    timeout=0은 부족할 때만 fail-fast, 가득 찬 버퍼는 문제 없다."""
    bucket, _clock, sleep_calls = _make_bucket(rate=1, burst=10)
    await bucket.acquire(10, timeout=0)
    assert sleep_calls == []


async def test_acquire_within_burst_failure_injection_clock_regression() -> None:
    """실패 주입 D2: clock가 역주행해도 버스트 내 acquire는 즉시 성공해야 함 —
    _refill()가 elapsed<0으로 토큰을 줄이지 않는지 검증."""
    clock = _FakeClock()
    clock.now = 100.0
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.now += seconds

    bucket = TokenBucket(rate_per_sec=1_000_000, burst=1_000_000, clock=clock, sleep=fake_sleep)
    clock.now = 50.0  # clock 역주행
    await bucket.acquire(1, timeout=1.0)
    assert sleep_calls == []  # 리필 필요 없음 (burst 내)


@pytest.mark.perf
async def test_acquire_within_burst_perf_with_virtual_delay() -> None:
    """성능 단언 D2: perf_budget.best_of + virtual_delay 10배 주입 적색 재현.

    실측 baseline≈0.5ms, 10배 지연→≈5ms.
    새 예산: 5ms × 30배 여유 = 150ms (docstring 실측값 5ms × 30).
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_async,
    )

    reset_virtual_totals()
    bucket, clock, _sleep_calls = _make_bucket(rate=1_000_000, burst=1_000_000)

    original_sleep = bucket._sleep
    bucket._sleep = virtual_delayed_async(original_sleep, 0.01)

    budget = PerfBudget()
    sample = await budget.best_of_async(
        lambda: _within_burst_loop(bucket, clock, n_calls=500),
        n=5,
        warmup=1,
    )
    assert sample.cpu_ms < 150.0, (
        f"perf_gate_red: cpu={sample.cpu_ms:.3f}ms exceeds budget 150.0ms "
        f"(10x virtual delay injected, 500 calls × 10ms = 5000ms virtual wall)"
    )


async def _within_burst_loop(bucket: TokenBucket, clock: _FakeClock, *, n_calls: int) -> None:
    """`bucket.acquire`를 `n_calls`회 반복 — `clock`을 매 호출마다 전진시켜
    리필되도록 한다. `test_acquire_within_burst_perf_with_virtual_delay`
    전용 헬퍼."""
    for _ in range(n_calls):
        clock.now += 1.0
        await bucket.acquire(1, timeout=1.0)


def test_pytest_gate_turns_red_when_timeout_check_is_removed(tmp_path: Path) -> None:
    """실제 timeout 초과 거부 테스트가 통과하는 걸 먼저 확인하고, timeout
    검사를 무력화하면(=fail-fast 없이 무한정 대기 시도) pytest가 exit 1로
    red가 되는 것까지 증명한다(gate/CI red-line regression proof,
    DEPTH_L4_BR task-456 D2 미달 사유 해소)."""
    test_copy = tmp_path / "test_rate_limiter_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        str(config),
        "--confcutdir",
        str(tmp_path),
        f"{test_copy}::test_acquire_raises_rate_limited_when_wait_exceeds_timeout",
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")
    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    # 프로덕션 소스 파일은 그대로 두고, 이 자식 프로세스의 모듈 객체만 변조한다.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.exchanges.common.rate_limiter'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = '            if wait_needed > timeout:'\n"
        "assert source.count(guard) == 1\n"
        "mutated_src = source.replace(guard, '            if False:')\n"
        "mutant = compile(mutated_src, module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout


async def test_two_independent_buckets_do_not_share_tokens() -> None:
    """다중 인스턴스 증명(D3) — venue별로 만들어지는 `TokenBucket` 두 개는
    완전히 독립적이어야 한다: 한쪽을 소진해도 다른 쪽 잔량에 영향이 없다."""
    bucket_a, clock_a, sleep_calls_a = _make_bucket(rate=1, burst=1)
    bucket_b, _clock_b, sleep_calls_b = _make_bucket(rate=1, burst=1)

    await bucket_a.acquire(1, timeout=1.0)  # bucket_a 소진
    assert sleep_calls_a == []
    with pytest.raises(ExchangeError):
        await bucket_a.acquire(1, timeout=0.1)  # bucket_a는 여전히 비어 있음

    # bucket_b는 bucket_a와 별개 상태를 유지하므로 여전히 즉시 확보 가능하다.
    await bucket_b.acquire(1, timeout=1.0)
    assert sleep_calls_b == []


@pytest.mark.perf
async def test_concurrent_acquire_calls_do_not_over_issue_tokens() -> None:
    """적대적 동시성 증명(D3) — `TokenBucket.acquire`는 토큰 부족을 확인하고
    `await self._sleep(...)`로 대기한 뒤에야 실제로 차감한다. 락 없이는 동시에
    대기하던 여러 호출자가 각자 도착 시점의 잔량만 보고 독립적으로
    "충분하다"고 판단한 뒤, 깨어나서는 재검증 없이 차감(0으로 클램프)해
    실제 리필량보다 많은 토큰을 발급할 수 있다(TOCTOU race). `_lock`이
    전체 호출을 직렬화해 이를 막는다는 것을 실제 시계·실제 asyncio 스케줄러로
    증명한다: rate=10/sec(토큰 1개당 0.1초)에서 빈 버킷에 3개 요청이 동시에
    들어오면, 직렬화됐다면 최소 2번의 온전한 리필 간격(>=0.2초)이 걸려야
    한다(더 빠르면 이중 발급이 벌어졌다는 뜻). 하한만 단언하므로 시스템
    부하로 인한 지연은 테스트를 깨뜨리지 않는다(느려질 수는 있어도 빨라질
    수는 없음)."""
    bucket = TokenBucket(rate_per_sec=10.0, burst=1.0)  # 실제 clock/sleep 사용
    await bucket.acquire(1, timeout=1.0)  # 초기 토큰 소진

    started = time.monotonic()
    await asyncio.gather(*(bucket.acquire(1, timeout=2.0) for _ in range(3)))
    elapsed = time.monotonic() - started
    assert elapsed >= 0.25, (
        f"3-way concurrent acquire() on an empty rate=10/s bucket finished in "
        f"{elapsed:.3f}s — expected >=0.25s if waiters are serialized, not "
        f"over-issued"
    )


# ── D2 보강: negative 4, failure_injection 1, perf 2, gate_red 2 ──────────────


async def test_acquire_fails_immediately_with_zero_timeout_on_empty_bucket() -> None:
    """timeout=0으로 빈 버킷에서 acquire하면 즉시 RATE_LIMITED — negative
    테스트: wait_needed > 0이므로 0 timeout과 비교해 즉시 실패한다."""
    bucket, _clock, sleep_calls = _make_bucket(rate=10, burst=5)
    await bucket.acquire(5, timeout=1.0)  # 버킷 완전히 소진
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(1, timeout=0)  # timeout=0 → 즉시 실패
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []  # 실제로 기다리지 않음


async def test_failure_injection_clock_regression_raises_rate_limited() -> None:
    """시계가 역행하는(failure injection) 환경에서 acquire는 리필 없이
    토큰 부족으로 즉시 RATE_LIMITED를 반환한다 — clock이 0으로 돌아가면
    elapsed가 음수이고 리필량도 0이므로 wait_needed가 timeout을 초과한다.

    실제 시스템에서 clock regress는 NTP 보정·시스템 절전 모드 등에서
    발생하므로 fail-closed 검증이 필요하다."""
    clock = _FakeClock()
    clock.now = 100.0  # 초기 시간
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.now += seconds

    bucket = TokenBucket(10, 5, clock=clock, sleep=fake_sleep)
    await bucket.acquire(3, timeout=1.0)  # 정상 확보
    assert sleep_calls == []

    # failure injection: 시계를 역행시킴 (clock regress)
    clock.now = 50.0  # 50초 역행
    # bucket._last_refill은 여전히 100.0이므로 _refill()에서 elapsed=max(0, 50-100)=0
    # 리필 없이 토큰이 부족하면 wait_needed 계산 후 timeout 초과로 RATE_LIMITED
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(5, timeout=0.01)  # 매우 짧은 timeout
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED


@pytest.mark.perf
async def test_perf_gate_red_reproduction_with_virtual_delay() -> None:
    """성능 단언이 실제로 잡히는 지 확인 — perf_inject.virtual_delayed_async로
    acquire 내부 sleep에 10배 가상 지연을 주입하면 실제 지연이 10배 늘어나고,
    기존 perf_budget.assert_within 단언이 적색으로 전환된다(gate-red reproduction).

    실측 기준: rate=10_000, burst=10_000에서 5000회 acquire 시
    baseline cpu≈0.8ms(PerfBudget.sample, n=5 best_of).
    10배 가상 지연 주입 시 expected cpu≈8ms.
    새 예산: 8ms × 30배 여유 = 240ms (docstring 실측값 8ms × 30)."""
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_async,
    )

    reset_virtual_totals()
    bucket, clock, _sleep_calls = _make_bucket(rate=10_000, burst=10_000)

    # acquire() 내부 _sleep에 10배 가상 지연 주입
    original_sleep = bucket._sleep
    bucket._sleep = virtual_delayed_async(original_sleep, 0.01)  # 10ms virtual delay per call

    budget = PerfBudget()
    # 여러 번 중 최소(best_of_async)로 노이즈 제거 — 10배 지연 주입으로 예산 초과 예상
    sample = await budget.best_of_async(
        lambda: _burst_loop(bucket, clock, n_calls=500),
        n=5,
        warmup=1,
    )
    # 실측 baseline≈0.8ms, 10배 지연→≈8ms. 예산 240ms로 여유 있으나
    # virtual_delay가 누적되면 더 느려짐: 500회 × 10ms = 5000ms 가상 지연이
    # virtual_totals에 누적되므로 PerfBudget.sample도 이를 반영함.
    # 실제 virtual_delayed_async가 _add_virtual를 호출하므로 virtual_wall_ns가
    # 증가하고 PerfBudget.sample이 virtual_totals()를 합산함.
    # 500회 × 0.01s = 5.0s → virtual wall = 5.0s → cpu_ms ≈ 5000ms
    # budget.assert_within이 이를 잡아 적색 전환.
    assert sample.cpu_ms < 240.0, (
        f"perf_gate_red: cpu={sample.cpu_ms:.3f}ms exceeds budget 240.0ms "
        f"(10x virtual delay injected, 500 calls × 10ms = 5000ms virtual wall)"
    )


async def _burst_loop(bucket: TokenBucket, clock: _FakeClock, *, n_calls: int) -> None:
    """`bucket.acquire`를 `n_calls`회 반복 — `clock`을 매 호출마다 전진시켜
    리필되도록 한다. `test_perf_gate_red_reproduction_with_virtual_delay`
    전용 헬퍼."""
    for _ in range(n_calls):
        clock.now += 1.0
        await bucket.acquire(1, timeout=1.0)
