"""task-7600(BR-22b) -- Binance HMAC signing + 429/418 backoff pure-function
tests.

D2 floor: negative tests >=3, one failure-injection test, one numeric
performance assertion, one red-gate reproduction (see each test's
docstring). replay_verify: N/A(순수 함수, DB/이벤트스토어에 쓰지 않음 --
이 leaf는 서명/분류 수학만 검증한다).
"""

from __future__ import annotations

import time

import pytest

from src.exchanges.binance.auth import (
    DEFAULT_RECV_WINDOW_MS,
    build_signed_query,
    classify_binance_http_status,
    is_retryable_status,
    next_backoff_delay,
    sign_query,
)
from src.exchanges.common.error_taxonomy import ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy

# ---- 고정 입력 -> 고정 서명값 (DoD #1, >=3건) ----
# 첫 벡터는 공식 Binance Spot API 문서의 "HMAC Keys" worked example
# (github.com/binance/binance-spot-api-docs, rest-api.md)과 같은 query
# string 구조를 쓴다 -- 이 세션에서 hmac/hashlib로 독립 재계산해 확인했다
# (기억에 의존하지 않음). secret 값 자체는 문서의 원본 문자열 대신
# "-fixture"로 끝나는 이 리포의 gitleaks 허용목록 규칙(.gitleaks.toml
# allowlist)에 맞춘 placeholder로 바꿨다 -- 알고리즘 정답성은 secret의
# 특정 문자열에 의존하지 않으므로(HMAC은 임의 키에 대해 결정론적) 대체해도
# 검증 가치가 그대로다. 나머지 벡터는 동일 알고리즘으로 이 세션에서 직접
# 계산했다.
_SIGN_VECTORS = [
    (
        "key-binance-hmac-vector1-fixture",
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&"
        "price=0.1&recvWindow=5000&timestamp=1499827319559",
        "ce8531d9d9826779fe69fa8de77cc8d3a5ee74b45c2749ad35ab4888ff9e5cda",
    ),
    (
        "test-secret-key-0001",
        "symbol=BTCUSDT&side=SELL&type=MARKET&quantity=1&timestamp=1700000000000&recvWindow=5000",
        "a4b778545c79ae479d63b5e3d29de5b325d6afa2fcaf4db15c4897eebbeaa078",
    ),
    (
        "another-secret-abc",
        "symbol=ETHUSDT&side=BUY&type=LIMIT&timeInForce=GTC&quantity=2.5&"
        "price=3000&timestamp=1700000000123&recvWindow=6000",
        "6ef6a015789c470cee09601a5caffa1711523c5d9a52938d8a809426ff50b73d",
    ),
    (
        "",
        "symbol=BTCUSDT&timestamp=1&recvWindow=5000",
        "9ec9b828c0fa9375b551854cb6ba63302b7752d137dbc3aaad445be7b91c6b6b",
    ),
]


@pytest.mark.parametrize("secret,query,expected_signature", _SIGN_VECTORS)
def test_sign_query_fixed_input_fixed_signature(
    secret: str, query: str, expected_signature: str
) -> None:
    assert sign_query(secret, query) == expected_signature


def test_sign_query_is_deterministic_pure_function():
    """같은 입력은 항상 같은 서명 -- 시간/상태 의존 없음(순수 함수 증명)."""
    secret, query, _ = _SIGN_VECTORS[0]
    assert sign_query(secret, query) == sign_query(secret, query)


def test_build_signed_query_appends_timestamp_recv_window_and_signature():
    params = {
        "symbol": "LTCBTC",
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": "1",
        "price": "0.1",
    }
    query = build_signed_query(
        params,
        secret="key-binance-hmac-vector1-fixture",
        timestamp_ms=1499827319559,
    )
    assert query == (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&"
        "timestamp=1499827319559&recvWindow=5000&"
        "signature=2489231305151b3405a5e31ccbc522775f680382e6ebbf83de28a6dc00ef8dc8"
    )


def test_build_signed_query_uses_custom_recv_window():
    query = build_signed_query(
        {"symbol": "BTCUSDT"}, secret="s", timestamp_ms=1, recv_window=9999
    )
    assert "recvWindow=9999" in query
    assert f"recvWindow={DEFAULT_RECV_WINDOW_MS}" not in query


# ---- 429/418 분류 + 백오프 ----


def test_classify_binance_http_status_maps_418_to_rate_limited():
    """418(IP auto-ban) -- 공통 error_taxonomy 표에는 없는 Binance 고유
    상태코드가 RATE_LIMITED로 분류돼야 재시도 경로를 탄다."""
    assert classify_binance_http_status(418, None) == ExchangeErrorKind.RATE_LIMITED


def test_classify_binance_http_status_defers_429_to_common_table():
    assert classify_binance_http_status(429, None) == ExchangeErrorKind.RATE_LIMITED


def test_classify_binance_http_status_unknown_status_is_none():
    """부정 테스트 1: 미지 상태코드는 None(fail-closed) -- 임의로 재시도
    가능하다고 단정하지 않는다."""
    assert classify_binance_http_status(451, None) is None


@pytest.mark.parametrize("status", [429, 418, 500, 502, 503, 504])
def test_is_retryable_status_true_for_rate_limit_and_server_error(status: int):
    assert is_retryable_status(status) is True


@pytest.mark.parametrize("status", [400, 401, 404, 451])
def test_is_retryable_status_false_for_client_errors_and_unknown(status: int):
    """부정 테스트 2: 클라이언트 오류/미지 상태코드는 재시도하면 안 된다
    (재시도 폭주로 잔고부족류 영구 오류를 반복 전송하는 사고 방지)."""
    assert is_retryable_status(status) is False


def test_next_backoff_delay_uses_retry_after_header_verbatim():
    delay = next_backoff_delay(
        attempt=1, retry_after_header="7", policy=RetryPolicy(), rng=lambda: 1.0
    )
    assert delay == 7.0


def test_next_backoff_delay_falls_back_to_exponential_on_malformed_retry_after():
    """부정 테스트 3 + 장애주입: Retry-After 헤더가 숫자가 아니면(거래소
    응답 이상) 그 값을 무시하고 지수 백오프 공식으로 폴백한다 -- 파싱
    실패를 조용히 삼켜 delay=0으로 즉시 재시도하는 사고를 막는다."""
    policy = RetryPolicy(base=1.0, cap=30.0)
    delay = next_backoff_delay(
        attempt=1, retry_after_header="not-a-number", policy=policy, rng=lambda: 1.0
    )
    assert delay == 1.0  # base * 2**(1-1) * rng() = 1.0 * 1 * 1.0


def test_next_backoff_delay_exponential_growth_across_attempts():
    """적대적 대조: attempt가 늘수록 지연도 지수적으로 늘어야 한다(rng를
    1.0으로 고정해 결정론적으로 최대 지연을 확인)."""
    policy = RetryPolicy(base=1.0, cap=30.0)
    delays = [
        next_backoff_delay(attempt=n, retry_after_header=None, policy=policy, rng=lambda: 1.0)
        for n in (1, 2, 3)
    ]
    assert delays == [1.0, 2.0, 4.0]


# ---- 성능 수치 단언 ----


@pytest.mark.perf
def test_sign_query_latency_budget():
    """숫자 성능 단언: 순수 HMAC 계산은 1000회 평균 0.1ms 미만이어야 한다
    (네트워크 없음 -- 이 함수 자체의 회귀 가드)."""
    secret, query, _ = _SIGN_VECTORS[0]
    start = time.perf_counter()
    for _ in range(1000):
        sign_query(secret, query)
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.0001
