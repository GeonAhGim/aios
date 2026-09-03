"""L4-12 — ResilientTransport: L4-11 5모듈(error_taxonomy/http_policy/
rate_limiter/circuit_breaker/clock_sync) 조립.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#L4-12

5모듈은 이미 시간/난수를 주입받는 순수 컴포넌트로 완성돼 있다(L4-11,
task-456, b638afc) — 이 모듈은 그것들의 공개 시그니처를 바꾸지 않고 하나의
`request()` 파이프라인으로 조립만 한다. venue별 서명·JSON 파싱·venue 오류
코드 해석은 호출부(예: bitget/adapter.py) 책임으로 남긴다 — 이 모듈은 venue
프로토콜을 모른다.

재시도 범위(§5.4) — `classify_body`가 만들어내는 바디 레벨(venue 오류코드)
실패는 이 파이프라인이 자동 재시도하지 않는다(단발 평가, 즉시 raise).
자동 재시도·백오프는 HTTP 상태코드(429/5xx)·네트워크 전송 실패에만
적용한다 — 잔고 부족 같은 바디 레벨 영구 오류를 거래소 문서 없이 재시도
루프에 태우면 사고로 이어진다. 재시도가 필요한 바디 레벨 실패(예: outbox
RETRY)는 상위 애플리케이션 계층 책임이다.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from src.exchanges.common.circuit_breaker import VenueCircuit
from src.exchanges.common.clock_sync import ServerClock
from src.exchanges.common.error_taxonomy import (
    ExchangeError,
    ExchangeErrorKind,
    classify_http,
    is_retryable,
)
from src.exchanges.common.http_policy import RetryPolicy, TimeoutBudget, backoff_delay
from src.exchanges.common.rate_limiter import TokenBucket

SendOnce = Callable[[], Awaitable[httpx.Response]]
ClassifyBody = Callable[[httpx.Response], ExchangeError | None]


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class ResilientTransport:
    """venue 공통 재시도·서킷브레이커·레이트리밋·클럭보정 파이프라인.

    `clock`은 프로퍼티로 노출한다 — 호출부가 서명 타임스탬프를
    `clock.now_ms()`로 만들고, 명시적으로 `clock.sync(...)`를 호출해
    오프셋을 갱신한다(매 요청 자동 동기화는 하지 않는다 — bitget/adapter.py
    docstring과 동일 판단: 정책은 호출부 책임).
    """

    def __init__(
        self,
        *,
        venue: str,
        retry_policy: RetryPolicy | None = None,
        timeout_budget: TimeoutBudget | None = None,
        rate_limiter: TokenBucket | None = None,
        circuit: VenueCircuit | None = None,
        clock: ServerClock | None = None,
        rng: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.venue = venue
        self.clock = clock or ServerClock()
        self._retry_policy = retry_policy or RetryPolicy()
        self._timeout_budget = timeout_budget or TimeoutBudget()
        self._rate_limiter = rate_limiter
        self._circuit = circuit
        self._rng = rng
        self._sleep = sleep

    async def request(
        self,
        send_once: SendOnce,
        *,
        classify_body: ClassifyBody | None = None,
    ) -> httpx.Response:
        """`send_once`를 재시도 정책에 따라 실행한다.

        `send_once`는 시도마다 새로 호출된다 — 서명 타임스탬프는 시도마다
        달라져야 하므로 호출부가 매 호출에서 헤더를 새로 계산해야 한다.
        HTTP 상태코드/네트워크 실패만 자동 재시도하고, 반환된 응답의
        바디 레벨 검증(`classify_body`)은 단발 평가다.
        """
        if self._circuit is not None and not self._circuit.allow():
            raise ExchangeError(
                ExchangeErrorKind.SERVER_ERROR,
                retryable=False,
                venue=self.venue,
                circuit_open=True,
                message=f"{self.venue} 서킷 OPEN — 요청 차단",
            )
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire(timeout=self._timeout_budget.total)

        try:
            response = await self._request_with_retry(send_once, classify_body)
        except ExchangeError:
            if self._circuit is not None:
                self._circuit.record(False)
            raise
        if self._circuit is not None:
            self._circuit.record(True)
        return response

    async def _request_with_retry(
        self, send_once: SendOnce, classify_body: ClassifyBody | None
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await send_once()
            except httpx.TransportError as exc:
                network_error = ExchangeError(
                    ExchangeErrorKind.TRANSIENT_NETWORK, venue=self.venue, message=str(exc)
                )
                if attempt >= self._retry_policy.max_attempts:
                    raise network_error from exc
                await self._wait(attempt, None)
                continue

            transport_error = self._classify_transport(response)
            if transport_error is None:
                return self._classify_or_return(response, classify_body)
            if not (
                is_retryable(transport_error.kind) and attempt < self._retry_policy.max_attempts
            ):
                raise transport_error
            await self._wait(attempt, transport_error.retry_after_sec)

    def _classify_transport(self, response: httpx.Response) -> ExchangeError | None:
        """HTTP 상태코드만 분류한다 — 바디는 보지 않는다(단발 평가는
        `_classify_or_return`이 처리)."""
        kind = classify_http(response.status_code, response.headers.get("Retry-After"))
        if kind is not None:
            return ExchangeError(
                kind,
                venue=self.venue,
                http_status=response.status_code,
                retry_after_sec=_retry_after_seconds(response),
            )
        if response.status_code >= 400:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE,
                retryable=False,
                venue=self.venue,
                http_status=response.status_code,
            )
        return None

    def _classify_or_return(
        self, response: httpx.Response, classify_body: ClassifyBody | None
    ) -> httpx.Response:
        if classify_body is not None:
            body_error = classify_body(response)
            if body_error is not None:
                raise body_error
        return response

    async def _wait(self, attempt: int, retry_after: float | None) -> None:
        delay = backoff_delay(self._retry_policy, attempt, retry_after, self._rng)
        await self._sleep(delay)
