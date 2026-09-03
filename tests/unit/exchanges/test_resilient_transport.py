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

import time

import httpx
import pytest

from src.exchanges.bitget.adapter import _BitgetHTTPClient
from src.exchanges.common.circuit_breaker import VenueCircuit
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy
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
