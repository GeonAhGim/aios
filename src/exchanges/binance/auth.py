"""BR-22b -- Binance HMAC-SHA256 request signing + 429/418 rate-limit backoff.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md steps 1-3 (Binance).

Signing (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `rest-api.md`
section "Signed (TRADE and USER_DATA) Endpoint security type"): a SIGNED
endpoint's query string must include `timestamp` and, when present,
`recvWindow`; `signature` is the HMAC-SHA256 hex digest of that exact query
string (every other param included verbatim, in the order sent) keyed by
the API secret. This adapter always sends SIGNED params as a query string
(matching `trading_mixin.py`'s existing `params=` convention, not a JSON
body). The official docs' "HMAC Keys" worked example's query-string shape
is reused as a signature test vector in
`tests/exchanges/binance/test_binance_auth.py` (independently recomputed
with `hmac`/`hashlib` in this session, not copied from memory) -- the
secret itself is swapped for a fixture-safe placeholder (this repo's
gitleaks allowlist convention, `.gitleaks.toml`), which does not affect
the signature math (HMAC is defined for any key).

Rate limiting (same docs, section "Rate limits"): 429 = per-endpoint
request-rate limit exceeded; repeatedly sending requests after a 429
escalates to 418 -- the IP is auto-banned for the ban duration named in
the response's `Retry-After` header. `error_taxonomy.classify_http`
(src/exchanges/common/error_taxonomy.py) has no entry for 418 -- no other
venue in this repo uses it -- so `classify_binance_http_status` below is
Binance's own narrow extension of that classifier rather than a change to
the shared table.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from urllib.parse import urlencode

from src.exchanges.common.error_taxonomy import ExchangeErrorKind, classify_http, is_retryable
from src.exchanges.common.http_policy import RetryPolicy, backoff_delay

DEFAULT_RECV_WINDOW_MS = 5000

# Binance-specific escalation beyond the standard 429 -- see module docstring.
_IP_AUTO_BAN_STATUS = 418


def sign_query(secret: str, query_string: str) -> str:
    """Pure function: HMAC-SHA256 hex digest of `query_string` keyed by
    `secret`. No network access and no live key required -- see the fixed
    input/output vectors in tests/exchanges/binance/test_binance_auth.py."""
    return hmac.new(
        secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_signed_query(
    params: Mapping[str, str],
    *,
    secret: str,
    timestamp_ms: int,
    recv_window: int = DEFAULT_RECV_WINDOW_MS,
) -> str:
    """Appends `timestamp`/`recvWindow` to `params` and returns the full
    query string (including `signature`) ready to send verbatim -- the
    signature covers exactly this string, so callers must not re-encode or
    reorder it afterward."""
    signed_params = dict(params)
    signed_params["timestamp"] = str(timestamp_ms)
    signed_params["recvWindow"] = str(recv_window)
    query_string = urlencode(signed_params)
    return f"{query_string}&signature={sign_query(secret, query_string)}"


def classify_binance_http_status(status: int, retry_after: str | None) -> ExchangeErrorKind | None:
    """Extends `error_taxonomy.classify_http` with Binance's 418 (IP
    auto-ban) status -- everything else defers to the common table."""
    if status == _IP_AUTO_BAN_STATUS:
        return ExchangeErrorKind.RATE_LIMITED
    return classify_http(status, retry_after)


def is_retryable_status(status: int) -> bool:
    """True for 429/418/5xx -- the statuses `factory.py`'s `_request` should
    back off and retry on. False (fail-closed default) for everything the
    classifier does not recognize, same posture as
    `error_taxonomy.is_retryable`."""
    kind = classify_binance_http_status(status, None)
    return kind is not None and is_retryable(kind)


def next_backoff_delay(
    *,
    attempt: int,
    retry_after_header: str | None,
    policy: RetryPolicy,
    rng: Callable[[], float],
) -> float:
    """Thin wrapper over `http_policy.backoff_delay` -- reuses the shared
    full-jitter exponential formula (D4 reuse principle) instead of
    reimplementing it; the only Binance-specific bit is parsing
    `Retry-After` (present on both 429 and 418 responses)."""
    retry_after: float | None = None
    if retry_after_header is not None:
        try:
            retry_after = float(retry_after_header)
        except ValueError:
            retry_after = None
    return backoff_delay(policy, attempt, retry_after, rng)
