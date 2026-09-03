"""DC-11 — `MarketDataProvider` SPI 공통 기반: rate-limit 토큰버킷·재시도·
정규화 훅.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2 모듈표 DC-11, §9.2 DC-11(선행 DC-5, task-1126 554f078 머지 완료).

`MarketDataProvider` Protocol(`ports/provider.py`)·`ProviderCapabilities`는
재정의하지 않는다(task-1187 decision) — 이 클래스는 그 Protocol을 구현할
DC-12 벤더 어댑터(bitget/kis)가 상속해 쓰는 공통 배관만 제공한다. 기존
`src/exchanges/**` 경로는 이 리프에서 한 줄도 고치지 않는다(import만 한다).

토큰버킷·재시도 메커니즘은 L4-11 내구성 모듈(`src/exchanges/common/
rate_limiter.py`·`http_policy.py`, task-456 b638afc)을 그대로 재사용하고
새 taxonomy를 만들지 않는다. 다만 SPI 경계를 넘나드는 예외는 DC-5의
`DataProviderError`/`DataProviderErrorCode`로 통일한다 — `ExchangeError`가
호출부(application 계층)로 새어 나가면 DC-5가 정의한 4종 taxonomy 밖의
예외를 다루게 되므로, 토큰버킷의 `ExchangeError`는 여기서 즉시
`DataProviderError(DATA_PROVIDER_RATE_LIMITED)`로 번역한다.

시간·난수는 전부 주입받는다(`clock`/`sleep`/`rng`) — `time.monotonic`/
`asyncio.sleep`/`random.random`을 직접 호출하지 않아 테스트가 실제 대기
없이 결정론적으로 검증한다(L4-11과 같은 패턴, task-423 d3227c9 계승).

재시도 범위 — `DataProviderError.retryable`(코드로 결정, `ports/provider.py`
`_RETRYABLE_CODES`)이 True인 경우만 재시도한다. `DATA_ENTITLEMENT_DENIED`·
`DATA_COVERAGE_MISSING`(영구/권한 오류)와 `DataProviderError`가 아닌 미지의
예외는 즉시 전파한다(무한 재시도 없음 — L4-11 `error_taxonomy.py`의
"모르면 재시도하지 않는다" fail-closed 원칙과 동일).
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.exchanges.common.error_taxonomy import ExchangeError
from src.exchanges.common.http_policy import RetryPolicy, backoff_delay
from src.exchanges.common.rate_limiter import TokenBucket
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    ProviderCandle,
    ProviderCapabilities,
    ProviderTick,
)

__all__ = ["BaseProviderAdapter", "NormalizationNotImplementedError"]

T = TypeVar("T")


class NormalizationNotImplementedError(NotImplementedError):
    """정규화 훅 미구현 — 원본 raw 데이터를 정규화 없이 그대로 흘려보내는
    대신 여기서 fail-closed 한다(§4.1 "조용한 0 채움 금지"와 같은 원칙:
    모르면 통과시키지 않는다)."""

    def __init__(self, adapter: object, hook: str) -> None:
        super().__init__(
            f"{type(adapter).__name__}.{hook}가 구현되지 않았다 — 원본을 정규화 "
            "없이 그대로 반환하는 것은 금지된다(하위 클래스가 이 훅을 오버라이드해야 한다)."
        )


class BaseProviderAdapter:
    """`MarketDataProvider` 구현체(DC-12)가 상속하는 공통 베이스.

    Protocol 메서드(`capabilities`/`list_instruments`/`fetch_candles`/
    `subscribe`)는 여기서 구현하지 않는다 — 그건 벤더별 DC-12 책임이다.
    이 클래스는 그 구현체가 내부에서 쓸 `call_with_retry`(속도제한+재시도)
    와 `normalize_candle`/`normalize_tick`(정규화 실패 안전망)만 준다.
    """

    def __init__(
        self,
        capabilities: ProviderCapabilities,
        *,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._capabilities = capabilities
        self._provider_id = capabilities.provider_id
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._rng = rng
        self._bucket = TokenBucket(
            rate_per_sec=float(capabilities.rate_limit.requests_per_second),
            burst=float(capabilities.rate_limit.burst),
            clock=clock,
            sleep=sleep,
        )

    async def call_with_retry(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        tokens: float = 1.0,
        acquire_timeout: float = 30.0,
    ) -> T:
        """토큰버킷으로 속도를 제한한 뒤 `op()`를 실행한다.

        토큰 확보는 호출당 1회다(버킷 소진 시 `TokenBucket.acquire`가
        내부적으로 대기·보충 후 반환한다 — 대기가 `acquire_timeout`을
        넘으면 `DataProviderError(DATA_PROVIDER_RATE_LIMITED)`로 즉시
        실패한다). 그 뒤 `op()`가 재시도 가능한 `DataProviderError`를
        던지면 `retry_policy.max_attempts`까지 지수 백오프로 재시도하고,
        영구 오류·미지의 예외·상한 도달 시에는 그대로 전파한다.
        """
        await self._acquire(tokens, acquire_timeout)
        attempt = 0
        while True:
            attempt += 1
            try:
                return await op()
            except DataProviderError as exc:
                if not exc.retryable or attempt >= self._retry_policy.max_attempts:
                    raise
                delay = backoff_delay(self._retry_policy, attempt, exc.retry_after_sec, self._rng)
                await self._sleep(delay)

    async def _acquire(self, tokens: float, timeout: float) -> None:
        try:
            await self._bucket.acquire(tokens, timeout=timeout)
        except ExchangeError as exc:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED,
                provider_id=self._provider_id,
                message=str(exc),
            ) from exc

    def normalize_candle(self, _raw: Any) -> ProviderCandle:
        """벤더 원시 캔들 → `ProviderCandle`. DC-12 구현체가 반드시
        오버라이드한다 — 기본 구현은 원본을 그대로 흘려보내지 않고 즉시
        `NormalizationNotImplementedError`로 실패한다."""
        raise NormalizationNotImplementedError(self, "normalize_candle")

    def normalize_tick(self, _raw: Any) -> ProviderTick:
        """벤더 원시 체결 → `ProviderTick`. `normalize_candle`과 동일한
        fail-closed 기본값."""
        raise NormalizationNotImplementedError(self, "normalize_tick")
