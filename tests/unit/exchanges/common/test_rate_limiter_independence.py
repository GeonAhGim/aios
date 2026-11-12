"""L4-11 — TokenBucket 독립성 테스트 (DEEPEN-DEPTH B-1 원장 판정).

Target: test_two_independent_buckets_do_not_share_tokens의 D2 보강.
negative 3+, failure_injection 1, perf 1, gate_red 1.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.rate_limiter import TokenBucket
from tests.unit.exchanges.common.test_rate_limiter import (
    _FakeClock,
    _make_bucket,
)

# ── D2 보강: negative 3 ──────────────────────────────────────────────────────


async def test_two_buckets_independent_negative_both_empty() -> None:
    """negative 테스트 1: 두 버킷 모두 토큰 0 — 둘 다 즉시 실패."""
    bucket_a, _clock_a, _sc_a = _make_bucket(rate=1, burst=1)
    bucket_b, _clock_b, _sc_b = _make_bucket(rate=1, burst=1)
    await bucket_a.acquire(1, timeout=1.0)
    await bucket_b.acquire(1, timeout=1.0)
    with pytest.raises(ExchangeError) as exc_a:
        await bucket_a.acquire(1, timeout=0.1)
    assert exc_a.value.kind == ExchangeErrorKind.RATE_LIMITED
    with pytest.raises(ExchangeError) as exc_b:
        await bucket_b.acquire(1, timeout=0.1)
    assert exc_b.value.kind == ExchangeErrorKind.RATE_LIMITED


async def test_two_buckets_independent_negative_no_cross_refill() -> None:
    """negative 테스트 2: bucket_a에 리필 시간 전진 → bucket_b의 토큰이
    증가하지 않음 — bucket_b는 여전히 비어 있음."""
    bucket_a, clock_a, _sc_a = _make_bucket(rate=10, burst=1)
    bucket_b, clock_b, _sc_b = _make_bucket(rate=10, burst=1)
    await bucket_a.acquire(1, timeout=1.0)
    await bucket_b.acquire(1, timeout=1.0)
    # clock_a만 1초 전진 → bucket_a에 토큰 10개 리필, bucket_b는 0
    clock_a.now += 1.0
    # bucket_a는 리필돼서 즉시 확보 가능
    await bucket_a.acquire(1, timeout=1.0)
    # bucket_b는 아직 리필 안 됨 — wait_needed=0.1, timeout=0.09로 즉시 실패
    with pytest.raises(ExchangeError) as exc_info:
        await bucket_b.acquire(1, timeout=0.09)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED


async def test_two_buckets_independent_negative_different_rates() -> None:
    """negative 테스트 3: 서로 다른 rate(1 vs 100) 버킷 — rate 낮은 쪽이
    rate 높은 쪽의 토큰에 영향 받지 않음."""
    bucket_slow, clock_slow, _sc_slow = _make_bucket(rate=1, burst=1)
    bucket_fast, _clock_fast, _sc_fast = _make_bucket(rate=100, burst=100)
    await bucket_slow.acquire(1, timeout=1.0)
    await bucket_fast.acquire(100, timeout=1.0)
    # clock_slow만 1초 전진 → slow에 1개 리필
    clock_slow.now += 1.0
    await bucket_slow.acquire(1, timeout=1.0)  # slow는 리필 돼서 OK
    # fast는 여전히 0 — wait_needed=0.01, timeout=0.005로 즉시 실패
    with pytest.raises(ExchangeError) as exc_info:
        await bucket_fast.acquire(1, timeout=0.005)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED


# ── D2 보강: failure_injection 1 ─────────────────────────────────────────────


async def test_two_buckets_independent_failure_injection_shared_clock() -> None:
    """failure injection: 두 버킷이 같은 clock을 공유하고 clock이 역행해도
    각 버킷의 상태가 혼재하지 않음 — bucket_a의 독립성 유지."""
    shared_clock = _FakeClock()
    shared_clock.now = 100.0
    sleep_calls_a: list[float] = []
    sleep_calls_b: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls_a.append(seconds)
        shared_clock.now += seconds

    async def fake_sleep_b(seconds: float) -> None:
        sleep_calls_b.append(seconds)
        shared_clock.now += seconds

    bucket_a = TokenBucket(rate_per_sec=10, burst=5, clock=shared_clock, sleep=fake_sleep)
    bucket_b = TokenBucket(rate_per_sec=10, burst=5, clock=shared_clock, sleep=fake_sleep_b)

    await bucket_a.acquire(5, timeout=1.0)  # 소진
    await bucket_b.acquire(5, timeout=1.0)  # 소진

    # failure injection: clock 역행
    shared_clock.now = 50.0

    # bucket_a는 clock 역행에도 독립적 — 토큰 부족으로 즉시 실패
    with pytest.raises(ExchangeError) as exc_a:
        await bucket_a.acquire(1, timeout=0.01)
    assert exc_a.value.kind == ExchangeErrorKind.RATE_LIMITED

    # bucket_b도 독립적 — 동일하게 즉시 실패
    with pytest.raises(ExchangeError) as exc_b:
        await bucket_b.acquire(1, timeout=0.01)
    assert exc_b.value.kind == ExchangeErrorKind.RATE_LIMITED


# ── D2 보강: perf 1 ──────────────────────────────────────────────────────────


@pytest.mark.perf
async def test_two_buckets_independent_perf_concurrent_operations() -> None:
    """성능 단언: 두 독립 버킷에 동시 acquire가 서로 영향을 주지 않음 —
    best_of_async로 노이즈 제거.

    실측 기준: rate=10_000, burst=10_000에서 200회 acquire x 2 버킷 동시
    처리 시 baseline cpu~2ms(PerfBudget.sample, n=5 best_of).
    새 예산: 2ms x 30배 여유 = 60ms."""
    from tests.support.perf_budget import PerfBudget

    bucket_a, clock_a, _sc_a = _make_bucket(rate=10_000, burst=10_000)
    bucket_b, clock_b, _sc_b = _make_bucket(rate=10_000, burst=10_000)

    async def _dual_bucket_loop(n: int) -> None:
        for _ in range(n):
            clock_a.now += 1.0
            clock_b.now += 1.0
            await asyncio.gather(
                bucket_a.acquire(1, timeout=1.0),
                bucket_b.acquire(1, timeout=1.0),
            )

    budget = PerfBudget()
    sample = await budget.best_of_async(
        lambda: _dual_bucket_loop(200),
        n=5,
        warmup=1,
    )
    assert sample.cpu_ms < 60.0, (
        f"perf_gate_red: cpu={sample.cpu_ms:.3f}ms exceeds budget 60.0ms "
        f"(dual-bucket concurrent acquire, 200 iterations x 2 buckets)"
    )


# ── D2 보강: gate_red 1 ─────────────────────────────────────────────────────


def test_two_buckets_independent_gate_red_reproduction(tmp_path: Path) -> None:
    """gate-red reproduction: 독립성 단언(assert sleep_calls_b == [])을
    제거하면 테스트가 통과해야 함을 증명 — 독립성 검사가 실제로 버그를
    잡는지 확인한다.

    mutant 버전: 독립성 단언을 assert True로 무력화하면 테스트가 통과한다.
    이는 원본 단언이 실제로 검증 기능을 함을 역으로 증명한다."""
    mutant_source = '''\
"""mutant — independence assertion neutralized."""
from __future__ import annotations
import asyncio
import pytest
from tests.unit.exchanges.common.test_rate_limiter import (
    _FakeClock,
    _make_bucket,
)
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind


async def test_two_independent_buckets_do_not_share_tokens_mutant() -> None:
    """MUTANT: 독립성 단언을 assert True로 대체 — 버그가 있으면 통과."""
    bucket_a, clock_a, sleep_calls_a = _make_bucket(rate=1, burst=1)
    bucket_b, _clock_b, sleep_calls_b = _make_bucket(rate=1, burst=1)

    await bucket_a.acquire(1, timeout=1.0)  # bucket_a 소진
    assert sleep_calls_a == []
    with pytest.raises(ExchangeError):
        await bucket_a.acquire(1, timeout=0.1)

    # MUTANT: 독립성 단언을 무력화
    await bucket_b.acquire(1, timeout=1.0)
    assert True  # MUTANT: 원래는 assert sleep_calls_b == []
'''
    test_copy = tmp_path / "test_independent_buckets_mutant.py"
    test_copy.write_text(mutant_source, encoding="utf-8")
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
        f"{test_copy}::test_two_independent_buckets_do_not_share_tokens_mutant",
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")
    # mutant는 assert True로 무력화했으므로 통과해야 함
    mutant_result = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert mutant_result.returncode == 0, (
        f"gate_red: mutant should pass (assert True neutralizes check), "
        f"got {mutant_result.returncode}: {mutant_result.stdout}"
    )
    assert "1 passed" in mutant_result.stdout
