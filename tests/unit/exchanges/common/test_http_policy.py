"""L4-11 — http_policy 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: full-jitter 범위, Retry-After 우선, cap, 주문 정책 max_attempts=1.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import threading
from pathlib import Path
from time import perf_counter

import pytest

from src.exchanges.common.http_policy import RetryPolicy, TimeoutBudget, backoff_delay


def test_full_jitter_stays_within_bounds() -> None:
    policy = RetryPolicy(max_attempts=4, base=0.25, cap=8.0)
    for attempt in range(1, 5):
        low = backoff_delay(policy, attempt, None, rng=lambda: 0.0)
        high = backoff_delay(policy, attempt, None, rng=lambda: 1.0)
        assert low == 0.0
        ceiling = min(policy.cap, policy.base * (2 ** (attempt - 1)))
        assert high == pytest.approx(ceiling)


def test_full_jitter_is_deterministic_given_rng() -> None:
    policy = RetryPolicy(base=0.25, cap=8.0)
    delay = backoff_delay(policy, attempt=2, retry_after=None, rng=lambda: 0.5)
    assert delay == pytest.approx(0.25 * 2 * 0.5)


def test_retry_after_takes_priority_over_backoff_formula() -> None:
    policy = RetryPolicy(base=0.25, cap=8.0)
    delay = backoff_delay(policy, attempt=4, retry_after=30.0, rng=lambda: 1.0)
    assert delay == 30.0


def test_backoff_respects_cap_at_high_attempt() -> None:
    policy = RetryPolicy(base=0.25, cap=8.0)
    delay = backoff_delay(policy, attempt=10, retry_after=None, rng=lambda: 1.0)
    assert delay == pytest.approx(8.0)


def test_negative_attempt_rejected() -> None:
    policy = RetryPolicy()
    with pytest.raises(ValueError):
        backoff_delay(policy, attempt=0, retry_after=None, rng=lambda: 0.5)


def test_order_submission_policy_is_single_attempt() -> None:
    """§5.4 — 주문 제출은 max_attempts=1(재시도는 outbox 책임)."""
    order_submit_policy = RetryPolicy(max_attempts=1)
    assert order_submit_policy.max_attempts == 1


def test_timeout_budget_defaults() -> None:
    budget = TimeoutBudget()
    assert budget.connect == 2.0
    assert budget.read == 5.0
    assert budget.total == 8.0


def test_backoff_delay_latency_budget() -> None:
    """`backoff_delay`는 매 재시도마다 호출되는 hot path — 로컬 회귀 예산이며
    SLO 단언은 아니다(headless worker 지침)."""
    policy = RetryPolicy(base=0.25, cap=8.0)
    rng = random.Random(1)
    backoff_delay(policy, 1, None, rng=rng.random)  # 콜드 스타트 워밍업 — 예산 밖.
    started = perf_counter()
    for i in range(50_000):
        backoff_delay(policy, (i % 4) + 1, None, rng=rng.random)
    elapsed = perf_counter() - started
    assert elapsed < 3.0, f"50000 backoff_delay calls took {elapsed:.3f}s (budget 3.0s)"


def test_pytest_gate_turns_red_when_cap_clamp_is_removed(tmp_path: Path) -> None:
    """실제 cap 준수 테스트가 통과하는 걸 먼저 확인하고, `min(policy.cap, ...)`
    클램프를 제거하면(=지수 백오프가 상한 없이 계속 커지면) pytest가 exit 1로
    red가 되는 것까지 증명한다(gate/CI red-line regression proof, DEPTH_L4_BR
    task-456 D2 미달 사유 해소)."""
    test_copy = tmp_path / "test_http_policy_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_backoff_respects_cap_at_high_attempt",
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
        "name = 'src.exchanges.common.http_policy'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = 'ceiling: float = min(policy.cap, policy.base * (2 ** (attempt - 1)))'\n"
        "assert source.count(guard) == 1\n"
        "mutant_src = 'ceiling: float = policy.base * (2 ** (attempt - 1))'\n"
        "mutant = compile(source.replace(guard, mutant_src), module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout


def test_adversarial_large_attempt_still_respects_cap() -> None:
    """적대적 증명(D3) — 호출부 버그/공격으로 비정상적으로 큰 attempt 값이
    들어와도(정상 경로는 max_attempts<=4) 상한(cap)을 벗어나지 않는다."""
    policy = RetryPolicy(base=0.25, cap=8.0)
    for attempt in (50, 100, 200):
        delay = backoff_delay(policy, attempt, None, rng=lambda: 1.0)
        assert delay == pytest.approx(8.0)
        delay_zero = backoff_delay(policy, attempt, None, rng=lambda: 0.0)
        assert delay_zero == 0.0


def test_multiple_retry_policies_do_not_cross_contaminate_via_shared_rng() -> None:
    """다중 인스턴스 증명(D3) — 서로 다른 `RetryPolicy` 두 개를 같은 rng
    객체로 번갈아 호출해도, 각 정책의 base/cap이 상대 정책 계산에 새지
    않는다(frozen dataclass, 순수 함수)."""
    order_policy = RetryPolicy(max_attempts=1, base=0.1, cap=1.0)
    stream_policy = RetryPolicy(max_attempts=4, base=0.25, cap=8.0)
    rng = random.Random(7)

    for attempt in range(1, 5):
        order_delay = backoff_delay(order_policy, 1, None, rng=rng.random)
        stream_delay = backoff_delay(stream_policy, attempt, None, rng=rng.random)
        assert 0.0 <= order_delay <= order_policy.cap
        assert 0.0 <= stream_delay <= min(
            stream_policy.cap, stream_policy.base * (2 ** (attempt - 1))
        )
    # 두 정책 dataclass 자체는 불변이므로 반복 호출 뒤에도 원래 값 그대로다.
    assert order_policy.base == 0.1 and order_policy.cap == 1.0
    assert stream_policy.base == 0.25 and stream_policy.cap == 8.0


def test_replaying_identical_rng_sequence_reproduces_identical_delays() -> None:
    """리플레이 증명(D3) — 감사/재현을 위해 동일한 시드로 rng를 재생하면
    동일한 지연 시퀀스가 나와야 한다(결정론, task-423 d3227c9 패턴)."""
    policy = RetryPolicy(base=0.25, cap=8.0)

    def _run() -> list[float]:
        rng = random.Random(42)
        return [backoff_delay(policy, attempt, None, rng=rng.random) for attempt in range(1, 5)]

    first_run = _run()
    replayed_run = _run()
    assert first_run == replayed_run


def test_concurrent_backoff_delay_calls_from_multiple_threads_are_independent() -> None:
    """다중 인스턴스 증명(D3, 스레드 버전) — 여러 스레드가 서로 다른 attempt로
    동시에 `backoff_delay`를 호출해도 결과가 뒤섞이지 않는다(순수 함수,
    공유 가변 상태 없음)."""
    policy = RetryPolicy(base=0.25, cap=8.0)
    results: list[float | None] = [None] * 20

    def _call(idx: int) -> None:
        attempt = (idx % 4) + 1
        results[idx] = backoff_delay(policy, attempt, None, rng=lambda: 1.0)

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(len(results))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i, delay in enumerate(results):
        attempt = (i % 4) + 1
        expected = min(policy.cap, policy.base * (2 ** (attempt - 1)))
        assert delay == pytest.approx(expected)
