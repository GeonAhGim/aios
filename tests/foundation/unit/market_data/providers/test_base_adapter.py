"""DC-11 `adapters/providers/base_adapter.py` 단위 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-11(DoD: 토큰버킷 결정론적 검증, 재시도가 일시 오류만 재시도하고
영구/미지 오류는 즉시 전파, 정규화 훅 미구현 시 negative test로 실패 단언).

시간(`clock`)·대기(`sleep`)·난수(`rng`)는 전부 페이크로 주입한다 —
`asyncio.sleep`을 실제로 기다리지 않고, 페이크 sleep이 페이크 clock을
같은 양만큼 전진시켜 `TokenBucket`의 보충 계산과 일관되게 맞춘다
(L4-11 task-423 d3227c9 패턴).
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from src.data.models.base import AssetClass
from src.exchanges.common.http_policy import RetryPolicy
from src.foundation.market_data.adapters.providers.base_adapter import (
    BaseProviderAdapter,
    NormalizationNotImplementedError,
)
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
    """`sleep(seconds)`를 실제로 기다리지 않고 페이크 clock을 그만큼
    전진시킨다 — `TokenBucket`이 대기 후 `_refill()`할 때 실제로 시간이
    지난 것처럼 보이게 한다."""

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
    rate: str = "2",
    burst: int = 2,
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


# ---- 토큰버킷: 결정론적 소진·대기·보충 ----------------------------------


async def test_token_bucket_waits_then_refills_deterministically() -> None:
    adapter, _clock, sleeper = _adapter(rate="2", burst=2)
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await adapter.call_with_retry(op) == "ok"  # 토큰 2 -> 1
    assert await adapter.call_with_retry(op) == "ok"  # 토큰 1 -> 0
    assert sleeper.calls == []

    # 버킷 소진 — 세 번째 호출은 0.5s 대기(= (1-0)/rate) 후 통과해야 한다.
    assert await adapter.call_with_retry(op) == "ok"
    assert sleeper.calls == [0.5]
    assert calls == 3


async def test_token_bucket_fails_fast_when_wait_exceeds_timeout() -> None:
    adapter, _clock, sleeper = _adapter(rate="1", burst=1)
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await adapter.call_with_retry(op, acquire_timeout=30.0) == "ok"  # 버킷 소진

    with pytest.raises(DataProviderError) as excinfo:
        await adapter.call_with_retry(op, acquire_timeout=0.1)  # 필요 대기 1s > 0.1s
    assert excinfo.value.code is DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED
    assert calls == 1  # op는 실행되지 않았다(토큰 확보 단계에서 실패)
    assert sleeper.calls == []  # fail-fast — 실제로 기다리지 않는다


# ---- 재시도: 일시 오류만, 영구/미지 오류는 즉시 전파 ---------------------


async def test_retries_transient_error_until_success() -> None:
    adapter, _clock, sleeper = _adapter(rate="100", burst=100, max_attempts=4)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, provider_id="test-provider"
            )
        return "recovered"

    assert await adapter.call_with_retry(op) == "recovered"
    assert attempts == 3
    assert len(sleeper.calls) == 2  # 실패 2회 -> 백오프 2회


async def test_permanent_error_propagates_without_retry() -> None:
    adapter, _clock, sleeper = _adapter(rate="100", burst=100, max_attempts=4)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        raise DataProviderError(
            DataProviderErrorCode.DATA_ENTITLEMENT_DENIED, provider_id="test-provider"
        )

    with pytest.raises(DataProviderError) as excinfo:
        await adapter.call_with_retry(op)
    assert excinfo.value.code is DataProviderErrorCode.DATA_ENTITLEMENT_DENIED
    assert attempts == 1  # 영구/권한 오류 — 재시도하지 않는다
    assert sleeper.calls == []


async def test_unknown_exception_propagates_without_retry() -> None:
    """`DataProviderError`가 아닌 예외는 재시도 대상 taxonomy 밖이므로
    fail-closed 하게 즉시 전파한다(모르면 재시도하지 않는다)."""
    adapter, _clock, sleeper = _adapter(rate="100", burst=100, max_attempts=4)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("unexpected")

    with pytest.raises(ValueError):
        await adapter.call_with_retry(op)
    assert attempts == 1
    assert sleeper.calls == []


async def test_retry_gives_up_after_max_attempts_no_infinite_loop() -> None:
    adapter, _clock, sleeper = _adapter(rate="100", burst=100, max_attempts=3)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        raise DataProviderError(
            DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE, provider_id="test-provider"
        )

    with pytest.raises(DataProviderError) as excinfo:
        await adapter.call_with_retry(op)
    assert excinfo.value.code is DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE
    assert attempts == 3  # max_attempts 상한에서 멈춘다 — 무한 재시도 없음
    assert len(sleeper.calls) == 2  # 마지막 시도 전에는 대기하지 않는다


# ---- 정규화 훅: 미구현 시 원본을 흘리지 않고 실패 ------------------------


class _IncompleteAdapter(BaseProviderAdapter):
    """정규화 훅을 오버라이드하지 않은 DC-12 미완성 구현을 흉내낸다."""


def test_normalize_candle_hook_fails_closed_when_unimplemented() -> None:
    incomplete = _IncompleteAdapter(_capabilities(rate="2", burst=2))
    with pytest.raises(NormalizationNotImplementedError):
        incomplete.normalize_candle({"raw": "vendor-payload"})


def test_normalize_tick_hook_fails_closed_when_unimplemented() -> None:
    incomplete = _IncompleteAdapter(_capabilities(rate="2", burst=2))
    with pytest.raises(NormalizationNotImplementedError):
        incomplete.normalize_tick({"raw": "vendor-payload"})


def test_normalize_candle_hook_can_be_overridden() -> None:
    class _Complete(BaseProviderAdapter):
        def normalize_candle(self, raw: Any) -> str:  # type: ignore[override]
            return f"normalized:{raw}"

    complete = _Complete(_capabilities(rate="2", burst=2))
    assert complete.normalize_candle("x") == "normalized:x"
