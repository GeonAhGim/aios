"""L4-12 — ResilientTransport(exchanges/common/transport.py) 단위 테스트 +
Bitget adapter의 clock_sync 서명 타임스탬프 배선 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#L4-12
DoD: 429/5xx/비JSON/서명-타임스탬프 4케이스. negative: 429에 재시도 없이
즉시 실패하면 FAIL(`test_429_retries_then_succeeds_with_backoff`가
`calls["n"] == 3`로 실제 재시도가 일어났음을 강제한다).

실키가 없으므로 e2e가 아니라 httpx.MockTransport/직접 send_once 스텁으로
검증한다(decision 참고).
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from src.exchanges.bitget.adapter import _BitgetHTTPClient
from src.exchanges.common.circuit_breaker import VenueCircuit
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.common.rate_limiter import TokenBucket
from src.exchanges.common.transport import ResilientTransport


async def test_429_retries_then_succeeds_with_backoff() -> None:
    """negative test — 429는 재시도 없이 즉시 실패하면 FAIL. 여기서는
    `calls["n"] == 3`(1회 실패 + 1회 실패 + 1회 성공)와 지수 백오프 sleep
    값으로 실제 재시도가 일어났음을 검증한다."""
    calls = {"n": 0}
    sleep_calls: list[float] = []

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    transport = ResilientTransport(
        venue="test",
        retry_policy=RetryPolicy(max_attempts=4, base=1.0, cap=30.0),
        rng=lambda: 1.0,
        sleep=fake_sleep,
    )

    response = await transport.request(send_once)

    assert response.status_code == 200
    assert calls["n"] == 3
    assert sleep_calls == [1.0, 2.0]


async def test_5xx_exhausts_retries_and_raises_retryable() -> None:
    calls = {"n": 0}

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    async def fake_sleep(seconds: float) -> None:
        return None

    transport = ResilientTransport(
        venue="test",
        retry_policy=RetryPolicy(max_attempts=3, base=0.01, cap=0.01),
        rng=lambda: 0.0,
        sleep=fake_sleep,
    )

    with pytest.raises(ExchangeError) as exc_info:
        await transport.request(send_once)

    assert exc_info.value.kind == ExchangeErrorKind.SERVER_ERROR
    assert exc_info.value.retryable is True
    assert calls["n"] == 3  # max_attempts만큼만 시도, 그 이상 재시도하지 않음


async def test_non_json_body_fails_closed_without_retry() -> None:
    """비JSON 본문(바디 레벨 검증 실패)은 error_taxonomy 기본값대로
    UNKNOWN_RESPONSE/retryable=False다 — 그리고 `classify_body`는
    ResilientTransport의 재시도 루프에 태우지 않는다(단발 평가): 잔고
    부족류 영구 오류를 재시도 폭주로 만들지 않기 위해서다."""
    calls = {"n": 0}

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<html>not json</html>")

    def classify_body(response: httpx.Response) -> ExchangeError | None:
        try:
            response.json()
        except ValueError:
            return ExchangeError(
                ExchangeErrorKind.UNKNOWN_RESPONSE, venue="test", http_status=response.status_code
            )
        return None

    transport = ResilientTransport(venue="test")

    with pytest.raises(ExchangeError) as exc_info:
        await transport.request(send_once, classify_body=classify_body)

    assert exc_info.value.kind == ExchangeErrorKind.UNKNOWN_RESPONSE
    assert exc_info.value.retryable is False
    assert calls["n"] == 1


async def test_circuit_open_blocks_before_any_send() -> None:
    """circuit_breaker(L4-11) 조립 검증 — OPEN 상태면 `send_once`를 아예
    호출하지 않는다."""
    circuit = VenueCircuit(failure_threshold=1, open_sec=999.0, clock=lambda: 0.0)
    circuit.allow()
    circuit.record(ok=False)  # 1회 실패로 OPEN 전이(threshold=1)
    assert circuit.state.value == "OPEN"

    calls = {"n": 0}

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    transport = ResilientTransport(venue="test", circuit=circuit)

    with pytest.raises(ExchangeError) as exc_info:
        await transport.request(send_once)

    assert exc_info.value.circuit_open is True
    assert calls["n"] == 0


# ---------- 서명 타임스탬프 clock_sync 보정 배선(Bitget adapter) ----------


async def test_bitget_signing_timestamp_reflects_clock_sync_offset() -> None:
    """`_BitgetHTTPClient._headers()`의 ACCESS-TIMESTAMP가 로컬 시계가
    아니라 `ResilientTransport.clock`(ServerClock, clock_sync.py)의
    오프셋 보정 시각을 쓰는지 검증한다. Bitget 서버시간을 로컬보다 60초
    앞선 값으로 스텁해 `sync_server_time()` 후 오프셋이 서명 타임스탬프에
    반영되는지 확인한다."""
    offset_seconds = 60

    def handler(request: httpx.Request) -> httpx.Response:
        server_time_ms = int(time.time() * 1000) + offset_seconds * 1000
        return httpx.Response(
            200,
            json={"code": "00000", "msg": "success", "data": {"serverTime": str(server_time_ms)}},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    client = _BitgetHTTPClient("key", "secret", "passphrase", http_client=http_client)

    await client.sync_server_time()
    headers = client._headers("GET", "/api/v2/spot/market/tickers")

    signed_ts_ms = int(headers["ACCESS-TIMESTAMP"])
    naive_ts_ms = int(time.time() * 1000)

    # 오프셋(+60s)이 반영돼 서명 타임스탬프가 로컬시각보다 충분히 앞서야
    # 한다(테스트 실행 지연 여유로 55s 이상만 확인).
    assert signed_ts_ms - naive_ts_ms > 55_000


# ---------- DEPTH D2 보강: 수치 성능 단언 + 게이트 적색 재현 ----------


class _SpyTokenBucket(TokenBucket):
    """`rate_limiter.acquire()` 호출 여부·횟수만 관찰하는 스파이.

    실제 토큰 계산 로직(L4-11 완성 컴포넌트)은 그대로 `super()`에 위임하고,
    호출 카운트만 얹는다 — 게이트 적색 재현 테스트가 "서킷 OPEN이면
    `_rate_limiter.acquire()`를 아예 호출하지 않는다"(`transport.py`의
    순서 불변식: circuit 체크가 rate_limiter보다 먼저)를 검증하는 데
    쓴다. 이 순서가 뒤바뀌면 차단된 요청마다 레이트리밋 토큰을 낭비하는
    회귀가 조용히 재발한다."""

    def __init__(self) -> None:
        super().__init__(rate_per_sec=1000.0, burst=1000.0, clock=lambda: 0.0)
        self.acquire_calls = 0

    async def acquire(self, n: float = 1, *, timeout: float) -> None:
        self.acquire_calls += 1
        await super().acquire(n, timeout=timeout)


async def test_circuit_open_never_consumes_rate_limit_token_gate_red_regression() -> None:
    """게이트 적색 재현 — circuit 체크(§`request()` 최상단)가 rate_limiter
    보다 먼저 실행돼야 한다는 순서 불변식의 회귀 테스트. `transport.py`의
    `request()`에서 두 체크의 순서가 뒤바뀌면(circuit이 rate_limiter
    뒤로 이동) 이 테스트가 `acquire_calls == 0` 단언에서 즉시 적색이
    된다 — 차단된 요청마다 한정된 레이트리밋 토큰을 소모하는 조용한
    자원 낭비 회귀를 CI에서 잡아낸다."""
    circuit = VenueCircuit(failure_threshold=1, open_sec=999.0, clock=lambda: 0.0)
    circuit.allow()
    circuit.record(ok=False)
    assert circuit.state.value == "OPEN"

    spy_bucket = _SpyTokenBucket()

    async def send_once() -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = ResilientTransport(venue="test", circuit=circuit, rate_limiter=spy_bucket)

    with pytest.raises(ExchangeError) as exc_info:
        await transport.request(send_once)

    assert exc_info.value.circuit_open is True
    assert spy_bucket.acquire_calls == 0  # 회귀 시 >=1이 되어 적색


async def test_circuit_open_fast_fail_latency_bound_under_load() -> None:
    """수치 성능 단언 — 서킷 OPEN 상태의 fast-fail 경로는 네트워크 I/O가
    전혀 없어야 하므로, 동시 200건을 처리해도 실측 벽시계 지연이 명시적
    상한(0.5s) 이내여야 한다. 이 경로에 실수로 blocking I/O나 `sleep`이
    섞여 들어가면(예: 서킷 OPEN인데도 백오프를 태우는 회귀) 이 상한을
    넘겨 테스트가 적색이 된다."""
    circuit = VenueCircuit(failure_threshold=1, open_sec=999.0, clock=lambda: 0.0)
    circuit.allow()
    circuit.record(ok=False)
    assert circuit.state.value == "OPEN"

    calls = {"n": 0}

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    transport = ResilientTransport(venue="test", circuit=circuit)

    started = time.perf_counter()
    results = await asyncio.gather(
        *(transport.request(send_once) for _ in range(200)),
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - started

    assert calls["n"] == 0  # send_once는 한 번도 실행되지 않았어야 함
    assert all(isinstance(r, ExchangeError) and r.circuit_open for r in results)
    assert elapsed < 0.5  # 수치 지연 상한(fast-fail에 실제 I/O가 섞이면 초과)


# ---------- DEPTH D3 보강: 적대적/다중 인스턴스 증명 ----------


async def test_malicious_negative_retry_after_is_clamped_without_retry_storm() -> None:
    """적대적 증명 — venue 서버(또는 중간자)가 음수 `Retry-After: -100`을
    보내 클라이언트가 즉시(대기 없이) 재시도를 폭주시키도록 유도하는
    시나리오. `backoff_delay`는 `max(0.0, retry_after)`로 클램프하므로
    음수 헤더가 들어와도 sleep 인자는 0.0으로 clamp되고, 재시도 횟수는
    여전히 `max_attempts`로 상한이 걸려 무한 재시도(DoS 증폭)로 이어지지
    않음을 검증한다."""
    calls = {"n": 0}
    sleep_calls: list[float] = []

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "-100"})

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    transport = ResilientTransport(
        venue="test",
        retry_policy=RetryPolicy(max_attempts=3, base=0.01, cap=0.01),
        sleep=fake_sleep,
    )

    with pytest.raises(ExchangeError) as exc_info:
        await transport.request(send_once)

    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert calls["n"] == 3  # 공격자가 음수 헤더를 보내도 상한 이상 재시도하지 않음
    assert sleep_calls == [0.0, 0.0]  # 음수 지연이 클램프돼 즉시-폭주로 악용되지 않음


async def test_shared_circuit_blocks_all_concurrent_transport_instances() -> None:
    """다중 인스턴스 증명 — 동일 venue를 쓰는 여러 워커 프로세스를
    흉내내어, 하나의 `VenueCircuit`을 공유하는 5개의 독립
    `ResilientTransport` 인스턴스를 만든다. 그중 한 인스턴스의 실패로
    회로가 OPEN되면, 이후 5개 인스턴스 전부(자기 자신 포함, 새로 만든
    인스턴스라도)가 동시 요청 시 예외 없이 전부 차단되어야 한다 — 회로
    상태가 인스턴스 로컬이 아니라 venue 전역으로 공유·강제됨을
    증명한다(우회 경로: "새 인스턴스를 만들면 회로를 리셋할 수 있다"는
    시도가 통하지 않아야 한다)."""
    shared_circuit = VenueCircuit(failure_threshold=1, open_sec=999.0, clock=lambda: 0.0)

    async def failing_send() -> httpx.Response:
        return httpx.Response(503)

    async def fake_sleep(seconds: float) -> None:
        return None

    tripping_transport = ResilientTransport(
        venue="test",
        circuit=shared_circuit,
        retry_policy=RetryPolicy(max_attempts=1),
        sleep=fake_sleep,
    )
    with pytest.raises(ExchangeError):
        await tripping_transport.request(failing_send)
    assert shared_circuit.state.value == "OPEN"

    instances = [ResilientTransport(venue="test", circuit=shared_circuit) for _ in range(5)]

    calls = {"n": 0}

    async def send_once() -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    results = await asyncio.gather(
        *(instance.request(send_once) for instance in instances),
        return_exceptions=True,
    )

    assert calls["n"] == 0  # 5개 인스턴스 어느 쪽도 실제 전송에 도달하지 못함
    assert all(isinstance(r, ExchangeError) and r.circuit_open for r in results)
