"""L4-11 — error_taxonomy 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: 429+Retry-After, 500/502/503, 401/403, 비JSON, 미지 코드 →
UNKNOWN_RESPONSE(retryable=False).
"""
from __future__ import annotations

import pytest

from src.core.exceptions import ExchangeAPIError
from src.exchanges.common.error_taxonomy import (
    ExchangeError,
    ExchangeErrorKind,
    classify_http,
    is_retryable,
)


def test_429_with_retry_after_classifies_rate_limited() -> None:
    assert classify_http(429, "3") == ExchangeErrorKind.RATE_LIMITED


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_classifies_server_error(status: int) -> None:
    assert classify_http(status, None) == ExchangeErrorKind.SERVER_ERROR


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_classifies_auth(status: int) -> None:
    assert classify_http(status, None) == ExchangeErrorKind.AUTH


def test_unknown_status_returns_none() -> None:
    """비JSON 본문 등으로 호출부가 상태코드를 신뢰할 수 없는 경우와 동일하게,
    표에 없는 상태코드는 None을 반환해 호출부가 UNKNOWN_RESPONSE로
    승격시키도록 강제한다."""
    assert classify_http(200, None) is None
    assert classify_http(999, None) is None


def test_unknown_response_is_not_retryable_fail_closed() -> None:
    """미지 응답(비JSON 본문 포함)은 반드시 retryable=False다 — 잔고 부족 같은
    영구 오류를 UNKNOWN_RESPONSE로 오분류해도 재시도 폭주로 이어지지 않아야
    한다."""
    kind = classify_http(999, None)
    assert kind is None
    err = ExchangeError(ExchangeErrorKind.UNKNOWN_RESPONSE, venue="bitget", http_status=999)
    assert err.retryable is False
    assert err.kind == ExchangeErrorKind.UNKNOWN_RESPONSE
    assert isinstance(err, ExchangeAPIError)


def test_insufficient_funds_is_not_retryable() -> None:
    """알려진 영구 오류(kind가 명시적으로 분류된 경우)도 재시도 대상이
    아니어야 한다 — 미지 코드뿐 아니라 알려진 영구 오류도 fail-closed."""
    err = ExchangeError(ExchangeErrorKind.INSUFFICIENT_FUNDS)
    assert err.retryable is False


@pytest.mark.parametrize(
    "kind",
    [
        ExchangeErrorKind.TRANSIENT_NETWORK,
        ExchangeErrorKind.RATE_LIMITED,
        ExchangeErrorKind.SERVER_ERROR,
    ],
)
def test_known_transient_kinds_are_retryable(kind: ExchangeErrorKind) -> None:
    assert is_retryable(kind) is True


def test_explicit_retryable_override_is_respected() -> None:
    err = ExchangeError(ExchangeErrorKind.SERVER_ERROR, retryable=False, circuit_open=True)
    assert err.retryable is False
    assert err.circuit_open is True
