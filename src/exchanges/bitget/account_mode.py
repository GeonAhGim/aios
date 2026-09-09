"""L4-31 — Bitget Classic(v2)/Unified(v3, UTA) account-mode detection + request assembly.

Spec: docs/design/02d_bitget_uta_v3_spec_v1.md (task-2514 empirical basis),
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B/§9

2026-09-09 real-key empirical test (task-2514 spec) — the signature/passphrase
authenticated normally, but the Classic v2 endpoint (`/api/v2/spot/account/info`)
was rejected with 40085
"You are in Unified Account mode, and the Classic Account API is not
supported". Because Bitget is migrating the account to UTA (Unified Trading
Account), the adapter must treat this code as a signal to switch subsequent
requests to the v3 (Unified) endpoint (per the official upgrade guide —
https://www.bitget.com/api-doc/classic/uta-api-upgrade-guide, 2026-09-09
investigation: "the signature mechanism is identical between v2/v3, and an
existing v2 API key supports v3 as-is").

40099 "exchange environment is incorrect" is unrelated to account mode — it is
returned when the demo header (`paptrading: 1`) was sent but the key is not a
demo key (same empirical test, task note). It is not an account-mode-switch
signal, but if it leaks through as UNKNOWN_RESPONSE the cause (not a demo key)
becomes unknowable, so error_codes.py classifies it explicitly — this module
owns only the code constant used for that judgment.

Account mode transitions monotonically over the lifetime of a single adapter
instance (CLASSIC -> UNIFIED). Because Bitget's UTA migration is a one-way
operation performed by a human on the dashboard (per official support docs,
investigated 2026-09-09), the same API key cannot revert from UNIFIED to
CLASSIC mid-session — if it ever did, that would mean a new key was issued,
which would be a new adapter instance.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError

# Empirically observed (2026-09-09, task-2514) body code. Whether the value
# itself is retryable is classified by error_codes.classify_body_code() — this
# module uses 40085 only for the judgment "should account mode switch to
# UNIFIED?".
UNIFIED_ACCOUNT_REQUIRED_CODE = "40085"
WRONG_ENVIRONMENT_CODE = "40099"


class BitgetAccountMode(str, Enum):
    CLASSIC = "classic"
    UNIFIED = "unified"


def requires_unified_switch(venue_code: str | None) -> bool:
    """Whether `venue_code` is the signal "Classic API rejected on a UTA account"."""
    return venue_code == UNIFIED_ACCOUNT_REQUIRED_CODE


# (method, path, params, body) — given a mode, assembles and returns the
# request matching that mode. If mode changes on retry, this function must be
# called again to rebuild body/path for the new mode (parameter renames like
# v2 "size" -> v3 "qty" exist, so resending the failed request's body as-is
# would be rejected again on v3).
RequestSpec = tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]
RequestBuilder = Callable[[BitgetAccountMode], RequestSpec]


class AccountModeAwareClient(Protocol):
    account_mode: BitgetAccountMode

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def account_aware_request(
    client: AccountModeAwareClient, build: RequestBuilder
) -> dict[str, Any]:
    """Sends the request matching `client.account_mode`, and if a 40085 is
    received while in CLASSIC state, that confirms this account is actually
    UTA, so `client.account_mode` is switched to UNIFIED and `build` is
    called again to reassemble the request in v3 shape, retried once (§8.3
    principle — do not propagate a failure as-is when it can be
    self-recovered from an already-known fact).

    This is safe even for fund-moving requests like place_order: 40085 means
    the request was rejected before reaching the matching engine (empirically
    observed — the signature passes, but the request itself is never routed
    to that API surface), so the retry cannot create a duplicate order.
    `client._request` raises `FatalExchangeError` only under this condition
    (adapter.py `_BitgetHTTPClient._request` — the retryable=False promotion
    path).
    """
    method, path, params, body = build(client.account_mode)
    try:
        return await client._request(method, path, params=params, body=body)
    except FatalExchangeError as exc:
        cause = exc.__cause__
        if (
            client.account_mode is BitgetAccountMode.CLASSIC
            and isinstance(cause, ExchangeError)
            and requires_unified_switch(cause.venue_code)
        ):
            client.account_mode = BitgetAccountMode.UNIFIED
            method, path, params, body = build(client.account_mode)
            return await client._request(method, path, params=params, body=body)
        raise
