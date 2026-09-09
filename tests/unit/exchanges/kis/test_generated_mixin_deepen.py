"""task-2785 DEEPEN of task-1929 (BR-12, ADR-2026-09-06-I D7).

DEPTH audit (task-2722, docs/audit/DEPTH_L4_BR.md #1929) found the original
commit's suite (tests/unit/scripts/test_kis_generate_adapters.py) below the
D2 floor for this leaf: it verifies the *generator* (deterministic rendering,
line cap, paper-sandbox guard emission) but never exercises a *generated*
method at runtime — no failure-injection test (simulated network/DB/parse
failure through a generated method call) and no numeric performance/
throughput assertion. This file adds exactly those two, against real
`KISGeneratedDomesticStock01Mixin` methods reached through `KISAdapter` — the
generator script and the committed generated/*.py files are not touched.
"""
from __future__ import annotations

import time

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.kis.adapter import KISAdapter

_TOKEN_PATH = "/oauth2/tokenP"
_SEARCH_STOCK_INFO_PATH = "/uapi/domestic-stock/v1/quotations/search-stock-info"
_ORDER_RESV_PATH = "/uapi/domestic-stock/v1/trading/order-resv"

_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": ""}


async def _instant_sleep(_seconds: float) -> None:
    """Skip `RetryPolicy`'s backoff wait — failure-injection tests should not
    block on the real 4-attempt retry delay (same technique as
    test_kis_overseas_deepen.py)."""


def _make_paper_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=http_client,
        sleep_fn=_instant_sleep,
    )


# ---------------------------------------------------------------------------
# 1) failure-injection through generated methods
# ---------------------------------------------------------------------------


async def test_generated_get_method_network_drop_exhausts_retries() -> None:
    """`search_stock_info_ctpf1002r` (generated GET method,
    domestic_stock_01_mixin.py) must propagate a `RetryableExchangeError`
    after `RetryPolicy.max_attempts` (4) network failures, not swallow them —
    the generator's own tests never call a generated method, so this branch
    had zero failure-injection coverage anywhere in the suite."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        call_count["n"] += 1
        raise httpx.ConnectError("network down", request=request)

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.search_stock_info_ctpf1002r({"PRDT_TYPE_CD": "300", "PDNO": "005930"})

    assert call_count["n"] == 4  # RetryPolicy default max_attempts


async def test_generated_post_method_malformed_json_raises_single_call() -> None:
    """`order_resv_ctsc0008u` (generated POST method guarded by
    `@require_paper_sandbox`) must raise on a non-JSON response body (e.g. an
    HTML error page) instead of returning a truncated/garbage dict — and
    `ResilientTransport` treats a body-level classification failure as
    non-retryable at the transport layer, so it must fire exactly once."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, text="<html>internal error</html>")

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.order_resv_ctsc0008u(
            {
                "CANO": "12345678",
                "ACNT_PRDT_CD": "01",
                "PDNO": "005930",
                "ORD_QTY": "1",
                "ORD_UNPR": "70000",
                "SLL_BUY_DVSN_CD": "02",
                "ORD_DVSN_CD": "00",
                "ORD_OBJT_CBLC_DVSN_CD": "10",
            }
        )

    assert business_calls["n"] == 1


async def test_generated_get_method_business_rejection_raises() -> None:
    """A `rt_cd != "0"` body (KIS's own business-rejection encoding) must
    surface as an exception through a generated method too, not just through
    the hand-written mixins — `_classify_body` promotes it before the
    generated method's caller ever sees the raw dict."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _SEARCH_STOCK_INFO_PATH:
            return httpx.Response(200, json={"rt_cd": "1", "msg1": "REJECTED"})
        raise AssertionError(f"unexpected path: {request.url.path}")

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.search_stock_info_ctpf1002r({"PRDT_TYPE_CD": "300", "PDNO": "005930"})


async def test_generated_method_token_fetch_failure_raises_fatal_before_any_call() -> None:
    """If token issuance itself fails (500), a generated method must not
    reach the business endpoint at all — `_fetch_token`'s conversion rule
    (`FatalExchangeError`) applies identically to generated call sites, not
    just the hand-written mixins that were covered before this leaf."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(500, json={"error": "server error"})
        raise AssertionError("business endpoint must not be reached after token failure")

    adapter = _make_paper_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.search_stock_info_ctpf1002r({"PRDT_TYPE_CD": "300", "PDNO": "005930"})


# ---------------------------------------------------------------------------
# 2) numeric performance/throughput assertion (normalized ratio, not absolute ms)
# ---------------------------------------------------------------------------


def _fast_success_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        captured.append(request)
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": {"a": "1"}})

    return handler


async def test_generated_method_call_overhead_bounded_vs_raw_request_baseline() -> None:
    """A generated method (`search_stock_info_ctpf1002r`) does nothing beyond
    `return await self._request(...)` — it must not add measurable overhead
    over calling `_request` directly. Instead of an absolute ms constant
    (flaky under shared-CI variance), the threshold is a ratio normalized
    against a same-process raw `_request` baseline measured immediately
    before (same decision as test_kis_overseas_deepen.py /
    test_submit_order_failure_injection.py)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(_fast_success_handler(captured))
    n = 200

    # Warm up — issue the token once so the cached-token path is what gets
    # timed below (an uncached first call would pollute the sample with a
    # one-off token round trip).
    await adapter._request(
        "GET", _SEARCH_STOCK_INFO_PATH, "CTPF1002R", params={"PRDT_TYPE_CD": "300"}
    )
    captured.clear()

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request(
            "GET", _SEARCH_STOCK_INFO_PATH, "CTPF1002R", params={"PRDT_TYPE_CD": "300"}
        )
    baseline_elapsed = time.perf_counter() - baseline_started

    generated_started = time.perf_counter()
    for _ in range(n):
        await adapter.search_stock_info_ctpf1002r({"PRDT_TYPE_CD": "300"})
    generated_elapsed = time.perf_counter() - generated_started

    ratio = generated_elapsed / baseline_elapsed
    budget_ratio = 3.0  # both do exactly one HTTP round trip; ~1x expected, generous headroom
    print(
        f"\ngenerated method call overhead: n={n} baseline={baseline_elapsed * 1000:.1f}ms "
        f"generated={generated_elapsed * 1000:.1f}ms ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"generated method call ({ratio:.2f}x raw _request baseline) regressed past the "
        f"{budget_ratio}x budget — a generated method should be a bare pass-through with no "
        "added per-call work."
    )
    assert len(captured) == 2 * n  # n baseline calls + n generated calls, none dropped/retried
