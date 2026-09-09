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
    bucket, _clock, sleep_calls = _make_bucket(rate=10, burst=5)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(6, timeout=100.0)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert sleep_calls == []


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
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_acquire_raises_rate_limited_when_wait_exceeds_timeout",
    ]
    env = dict(
        os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8"
    )
    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
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
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
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
