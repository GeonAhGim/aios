"""L4-11 — 거래소 오류 분류 enum + 분류 함수.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

Fail-closed 원칙 — 미지의 HTTP 상태코드/거래소 오류코드는 `UNKNOWN_RESPONSE`로
분류하고 `retryable=False`로 취급한다(잔고 부족 같은 영구 오류를 재시도하는
사고를 막기 위해, "모른다"는 "재시도해도 된다"가 아니라 "재시도하면 안 된다"로
해석한다).
"""
from __future__ import annotations

from enum import Enum

from src.core.exceptions import ExchangeAPIError


class ExchangeErrorKind(str, Enum):
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    AUTH = "AUTH"
    CLOCK_SKEW = "CLOCK_SKEW"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INVALID_ORDER = "INVALID_ORDER"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    DUPLICATE_CLIENT_ID = "DUPLICATE_CLIENT_ID"
    MARKET_CLOSED = "MARKET_CLOSED"
    UNKNOWN_RESPONSE = "UNKNOWN_RESPONSE"


# HTTP 상태코드 → kind. 이 표에 없는 상태코드는 classify_http()가 None을
# 반환하고, 호출부는 이를 UNKNOWN_RESPONSE(retryable=False)로 취급해야 한다.
_STATUS_KIND: dict[int, ExchangeErrorKind] = {
    401: ExchangeErrorKind.AUTH,
    403: ExchangeErrorKind.AUTH,
    429: ExchangeErrorKind.RATE_LIMITED,
    500: ExchangeErrorKind.SERVER_ERROR,
    502: ExchangeErrorKind.SERVER_ERROR,
    503: ExchangeErrorKind.SERVER_ERROR,
    504: ExchangeErrorKind.SERVER_ERROR,
}

# 이 kind들만 재시도 가능하다고 간주한다. UNKNOWN_RESPONSE는 절대 포함하지
# 않는다(fail-closed).
_RETRYABLE_KINDS = frozenset(
    {
        ExchangeErrorKind.TRANSIENT_NETWORK,
        ExchangeErrorKind.RATE_LIMITED,
        ExchangeErrorKind.SERVER_ERROR,
    }
)


def classify_http(status: int, retry_after: str | None) -> ExchangeErrorKind | None:
    """HTTP 상태코드를 ExchangeErrorKind로 분류한다.

    표에 없는 상태코드는 None을 반환한다 — 호출부가 UNKNOWN_RESPONSE로
    승격시킬지 결정한다(이 함수 스스로 fallback 값을 고르지 않는다).
    `retry_after`는 429 여부와 무관하게 값 자체를 바꾸지 않는다 — 지연 계산은
    http_policy.backoff_delay()의 책임이다.
    """
    del retry_after  # 분류에는 쓰이지 않는다(백오프 지연 계산에서만 사용).
    return _STATUS_KIND.get(status)


def is_retryable(kind: ExchangeErrorKind) -> bool:
    """kind가 재시도 가능한지 여부. UNKNOWN_RESPONSE는 항상 False."""
    return kind in _RETRYABLE_KINDS


class ExchangeError(ExchangeAPIError):
    """분류된 거래소 오류. HTTP/거래소 응답 파싱 지점에서 생성한다."""

    def __init__(
        self,
        kind: ExchangeErrorKind,
        *,
        retryable: bool | None = None,
        venue: str | None = None,
        http_status: int | None = None,
        venue_code: str | None = None,
        retry_after_sec: float | None = None,
        circuit_open: bool = False,
        message: str | None = None,
    ) -> None:
        self.kind = kind
        self.retryable = is_retryable(kind) if retryable is None else retryable
        self.venue = venue
        self.http_status = http_status
        self.venue_code = venue_code
        self.retry_after_sec = retry_after_sec
        self.circuit_open = circuit_open
        super().__init__(
            message or f"거래소 오류: kind={kind.value} venue={venue} status={http_status}"
        )
