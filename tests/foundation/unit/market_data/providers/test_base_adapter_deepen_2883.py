"""DC-11 `adapters/providers/base_adapter.py` — DEEPEN(task-2883,
DEPTH_DC_RD 소급감사 task-2726) D1 -> D3 증빙.

기존 `test_base_adapter.py`는 토큰버킷/재시도/정규화 훅의 핵심 의미론만
결정론적으로 증명했다(D1/D2) — 감사에서 "수치 성능 단언 없음(현재
`sleeper.calls` 카운트뿐), 게이트 적색 재현 없음, D3 요소 없음"으로
지적됐다. 이 파일이 그 부족분을 채운다. `base_adapter.py`는 한 줄도 고치지
않는다 — 새 기능 없음, 깊이만 올린다.

1. 성능 단언(수치) — 대량 `call_with_retry` 호출의 실측 벽시계 시간이 절대
   예산 내에 있음을 `time.perf_counter`로 증명한다(호출 횟수 카운트가
   아니라 실제 경과 시간).
2. 게이트 적색 재현 — 토큰고갈 fail-fast -> 성공 -> 일시오류 복구 ->
   영구오류 전파 -> 미지예외 전파를 한 인스턴스에서 순서대로 재생하며
   각 단계가 이전 단계의 상태(버킷 잔량·시도 카운터)를 오염시키지 않음을
   증명하고, `retry_after_sec`이 백오프 공식을 실제로 덮어쓰는지(구현이
   이를 무시하면 이 테스트가 적색이 된다) 재현한다.
3. D3 — 서로 다른 어댑터 인스턴스 간 동시 호출이 서로 오염되지 않음, 단일
   인스턴스 버킷에 대한 동시 다중 호출이 토큰을 음수로 만들거나 이중
   소비하지 않음, 고정시드 5개로 무작위 재시도 시퀀스의 백오프가 항상
   `[0, cap]` 불변식을 지킴, 동일 시드 재생이 결정론적으로 동일한 지연
   시퀀스를 만들어냄을 증명한다.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.exchanges.common.http_policy import RetryPolicy
from src.foundation.market_data.adapters.providers.base_adapter import BaseProviderAdapter
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    ProviderCapabilities,
    RateLimitSpec,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _FakeSleeper:
    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.now += seconds


def _capabilities(*, rate: str, burst: int) -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_id="test-provider",
        asset_classes=frozenset({AssetClass.CRYPTO}),
        timeframes=frozenset({Timeframe.M1}),
        history_from=None,
        realtime=True,
        delayed_seconds=0,
        max_symbols_per_request=10,
        rate_limit=RateLimitSpec(requests_per_second=Decimal(rate), burst=burst),
    )


def _adapter(
    *,
    rate: str = "1000",
    burst: int = 1000,
    max_attempts: int = 4,
    rng: Callable[[], float] = lambda: 0.0,
) -> tuple[BaseProviderAdapter, _FakeClock, _FakeSleeper]:
    clock = _FakeClock()
    sleeper = _FakeSleeper(clock)
    adapter = BaseProviderAdapter(
        _capabilities(rate=rate, burst=burst),
        retry_policy=RetryPolicy(max_attempts=max_attempts, base=0.1, cap=1.0),
        clock=clock,
        sleep=sleeper,
        rng=rng,
    )
    return adapter, clock, sleeper


# ---- 1. 성능 단언(수치) — 벽시계 예산 -------------------------------------


async def test_many_successful_calls_stay_within_wall_clock_budget() -> None:
    """버킷이 고갈되지 않는 2,000회 연속 성공 호출의 실측 경과 시간이
    예산(2초) 내여야 한다 — 호출 횟수만 세는 게 아니라 락 획득·`_refill`
    계산·예외 분기 오버헤드가 O(n^2)로 새지 않음을 실제 시계로 증명한다."""
    adapter, _clock, sleeper = _adapter(rate="100000", burst=100000)

    async def op() -> int:
        return 1

    started = time.perf_counter()
    for _ in range(2000):
        assert await adapter.call_with_retry(op) == 1
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"2000회 호출에 {elapsed:.3f}s 소요 -- 예산(2.0s) 초과"
    assert sleeper.calls == []  # 버킷 고갈 없음 -- 대기 없이 전부 즉시 통과


async def test_concurrent_calls_stay_within_wall_clock_budget() -> None:
    """500개 동시 호출(`asyncio.gather`)도 절대 시간 예산 내에서 끝나야
    한다 -- 락 경합이 순차 실행 수준으로 직렬화되더라도 예산을 넘기면 안 된다."""
    adapter, _clock, sleeper = _adapter(rate="100000", burst=100000)

    async def op() -> int:
        return 1

    started = time.perf_counter()
    results = await asyncio.gather(*[adapter.call_with_retry(op) for _ in range(500)])
    elapsed = time.perf_counter() - started

    assert results == [1] * 500
    assert elapsed < 2.0, f"500개 동시 호출에 {elapsed:.3f}s 소요 -- 예산(2.0s) 초과"
    assert sleeper.calls == []


# ---- 2. 게이트 적색 재현 -- 다단계 시나리오 + retry_after_sec -------------


async def test_multi_stage_lifecycle_no_state_leak_between_stages() -> None:
    """토큰고갈 fail-fast -> 성공 -> 일시오류 복구 -> 영구오류 전파 ->
    미지예외 전파를 한 인스턴스에서 순서대로 재생한다. 각 단계는 독립된
    호출이어야 하며, 이전 단계에서 남은 시도 카운터·백오프 상태가 다음
    단계로 새어 들어가면 안 된다(예: 영구오류 직후의 성공 호출이 이전
    재시도 이력 때문에 불필요하게 대기하면 안 된다)."""
    adapter, _clock, sleeper = _adapter(rate="1", burst=1, max_attempts=3)

    # 0단계: 시작 토큰(burst=1)을 즉시 소비해 버킷을 고갈시킨다.
    async def prime() -> str:
        return "primed"

    assert await adapter.call_with_retry(prime, acquire_timeout=30.0) == "primed"
    assert sleeper.calls == []

    # 1단계: 버킷 고갈 상태에서 짧은 timeout -> fail-fast(op 미실행).
    async def never_called() -> str:
        raise AssertionError("토큰 확보 실패 시 op가 호출되면 안 된다")

    with pytest.raises(DataProviderError) as excinfo:
        await adapter.call_with_retry(never_called, acquire_timeout=0.0)
    assert excinfo.value.code is DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED
    assert sleeper.calls == []

    # 2단계: 대기를 감수하고 성공 -- 1단계의 실패가 버킷 잔량을 축내지 않았어야
    # 정확히 1s 대기 후(burst=1, rate=1) 통과한다.
    async def ok() -> str:
        return "ok"

    assert await adapter.call_with_retry(ok, acquire_timeout=30.0) == "ok"
    assert sleeper.calls == [1.0]

    # 3단계: 일시 오류 1회 후 복구 -- 시도 카운터가 0부터 시작해야 attempts==2.
    attempts_stage3 = 0

    async def transient_then_ok() -> str:
        nonlocal attempts_stage3
        attempts_stage3 += 1
        if attempts_stage3 == 1:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, provider_id="test-provider"
            )
        return "recovered"

    assert await adapter.call_with_retry(transient_then_ok, acquire_timeout=30.0) == "recovered"
    assert attempts_stage3 == 2

    # 4단계: 영구 오류 -- 즉시 전파, 3단계의 재시도 이력이 새어 들어와
    # 추가로 재시도되면 안 된다(attempts==1).
    attempts_stage4 = 0

    async def permanent() -> str:
        nonlocal attempts_stage4
        attempts_stage4 += 1
        raise DataProviderError(
            DataProviderErrorCode.DATA_ENTITLEMENT_DENIED, provider_id="test-provider"
        )

    with pytest.raises(DataProviderError) as excinfo:
        await adapter.call_with_retry(permanent, acquire_timeout=30.0)
    assert excinfo.value.code is DataProviderErrorCode.DATA_ENTITLEMENT_DENIED
    assert attempts_stage4 == 1

    # 5단계: 미지의 예외 -- 즉시 전파, 앞선 영구오류 처리가 이 경로를
    # 오염시키지 않는다(attempts==1).
    attempts_stage5 = 0

    async def unknown() -> str:
        nonlocal attempts_stage5
        attempts_stage5 += 1
        raise RuntimeError("unexpected vendor failure")

    with pytest.raises(RuntimeError):
        await adapter.call_with_retry(unknown, acquire_timeout=30.0)
    assert attempts_stage5 == 1


async def test_retry_after_sec_overrides_backoff_formula() -> None:
    """서버가 지정한 `retry_after_sec`은 `base`/`cap`/`rng` 백오프 공식을
    덮어써야 한다(§ http_policy.backoff_delay 계약). 이 값을 무시하고 항상
    지수 백오프를 계산하도록 구현이 퇴행하면 이 테스트가 적색이 된다 --
    `rng`를 1.0(최대치)으로 고정해 공식대로였다면 `cap`(1.0s)이 나왔을
    자리에 `retry_after_sec`(0.05s)이 그대로 쓰였는지로 구분한다."""
    adapter, _clock, sleeper = _adapter(rate="100", burst=100, max_attempts=3, rng=lambda: 1.0)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED,
                provider_id="test-provider",
                retry_after_sec=0.05,
            )
        return "ok"

    assert await adapter.call_with_retry(op) == "ok"
    assert sleeper.calls == [0.05]  # cap(1.0) 공식이 아니라 서버 지정값 그대로


# ---- 3. D3 -- 동시 다중 인스턴스/고정시드 fuzz/재생 결정론 ----------------


async def test_concurrent_independent_adapters_do_not_cross_contaminate() -> None:
    """서로 다른 어댑터 인스턴스(각자 버킷·정책·카운터를 가짐) 3개를
    동시에 굴려도 한 인스턴스의 재시도/실패가 다른 인스턴스의 결과나
    시도 횟수에 영향을 주면 안 된다."""
    ok_adapter, _c1, sleeper1 = _adapter(rate="1000", burst=1000)
    transient_adapter, _c2, sleeper2 = _adapter(rate="1000", burst=1000, max_attempts=5)
    permanent_adapter, _c3, sleeper3 = _adapter(rate="1000", burst=1000)

    transient_attempts = 0

    async def always_ok() -> str:
        return "ok"

    async def transient_twice_then_ok() -> str:
        nonlocal transient_attempts
        transient_attempts += 1
        if transient_attempts <= 2:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, provider_id="test-provider"
            )
        return "recovered"

    async def always_permanent() -> None:
        raise DataProviderError(
            DataProviderErrorCode.DATA_ENTITLEMENT_DENIED, provider_id="test-provider"
        )

    results = await asyncio.gather(
        ok_adapter.call_with_retry(always_ok),
        transient_adapter.call_with_retry(transient_twice_then_ok),
        permanent_adapter.call_with_retry(always_permanent),
        return_exceptions=True,
    )

    assert results[0] == "ok"
    assert results[1] == "recovered"
    assert isinstance(results[2], DataProviderError)
    assert results[2].code is DataProviderErrorCode.DATA_ENTITLEMENT_DENIED
    assert transient_attempts == 3
    assert sleeper1.calls == []  # 성공 인스턴스는 전혀 대기하지 않았다
    assert sleeper3.calls == []  # 영구오류 인스턴스는 재시도하지 않았다
    assert len(sleeper2.calls) == 2  # 일시오류 인스턴스만 2회 백오프


async def test_concurrent_calls_on_shared_bucket_never_oversubscribe() -> None:
    """burst=3인 단일 버킷에 5개 동시 호출을 걸면, `TokenBucket._lock`이
    직렬화해 5개 모두 성공하되 순간 소비량이 burst를 넘어서면 안 된다
    (TOCTOU 이중소비 없음). 5개 모두 정확히 1개의 토큰을 쓰므로 결과는
    5개 전부 성공이어야 하고, 부족분(2개)만큼만 대기가 발생해야 한다."""
    adapter, _clock, sleeper = _adapter(rate="1", burst=3)

    call_count = 0

    async def op() -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    results = await asyncio.gather(*[adapter.call_with_retry(op) for _ in range(5)])

    assert sorted(results) == [1, 2, 3, 4, 5]
    assert call_count == 5
    # burst=3까지는 즉시 통과, 나머지 2개만 부족분(각 1개 토큰, rate=1)만큼 대기.
    assert len(sleeper.calls) == 2
    assert all(delay > 0 for delay in sleeper.calls)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
async def test_fuzz_fixed_seed_backoff_stays_within_policy_bounds(seed: int) -> None:
    """고정시드 5개로 무작위 길이(1~3회)의 일시오류 시퀀스를 재생한다.
    `backoff_delay`가 산출하는 모든 지연이 `[0, cap]` 불변식을 벗어나면
    안 되고, `max_attempts`를 넘는 무한 재시도가 없어야 한다."""
    rng_source = random.Random(seed)
    adapter, _clock, sleeper = _adapter(
        rate="1000", burst=1000, max_attempts=4, rng=rng_source.random
    )
    fail_count = rng_source.randint(1, 3)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= fail_count:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, provider_id="test-provider"
            )
        return "ok"

    result = await adapter.call_with_retry(op)

    assert result == "ok"
    assert attempts == fail_count + 1
    assert len(sleeper.calls) == fail_count
    assert all(0.0 <= delay <= 1.0 for delay in sleeper.calls)  # cap=1.0(RetryPolicy 기본 상한)


async def test_replay_with_same_seed_is_deterministic() -> None:
    """동일 시드로 두 번 재생하면 지연 시퀀스가 정확히 같아야 한다 --
    `call_with_retry`가 숨겨진 전역 난수/시각 상태를 참조하지 않고 오직
    주입된 `rng`/`clock`/`sleep`만 쓴다는 증거."""

    async def _run(seed: int) -> list[float]:
        rng_source = random.Random(seed)
        adapter, _clock, sleeper = _adapter(
            rate="1000", burst=1000, max_attempts=5, rng=rng_source.random
        )
        attempts = 0

        async def op() -> str:
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise DataProviderError(
                    DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, provider_id="test-provider"
                )
            return "ok"

        assert await adapter.call_with_retry(op) == "ok"
        return sleeper.calls

    first = await _run(seed=42)
    second = await _run(seed=42)
    assert first == second
    assert len(first) == 3
