"""BR-22b QA -- fixed signature vectors (DoD 1), negative + perf evidence.

Expected values were computed outside the module with stdlib `hmac`/`hashlib`
(not by calling `sign_query`), so these are independent reference vectors.
"""

from __future__ import annotations

from src.exchanges.binance.auth import build_signed_query, sign_query


def test_fixed_vector_short_query():
    # Reference: hmac.new(b"k", b"a=1", sha256).hexdigest()
    assert (
        sign_query("k", "a=1") == "310f57de49873563b85599a4aaa688883c5c6ebc7d3925020d99379d1a4d0af8"
    )


def test_fixed_vector_empty_inputs():
    # Reference: hmac.new(b"", b"", sha256).hexdigest()
    assert sign_query("", "") == "b613679a0814d9ec772f95d778c35fc5ff1697c493715653c6c712144292c5ad"


def test_fixed_vector_full_signed_query():
    # Reference: hmac.new(b"sec", b"symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000000", sha256)
    q = build_signed_query("sec", {"symbol": "BTCUSDT"}, timestamp_ms=1700000000000)
    assert q == (
        "symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000000"
        "&signature=c41c8219bc916727333c4ade7148d62cb17d7b93b719a4ddae09099dc662f2b6"
    )


def test_negative_wrong_secret_changes_signature():
    # Negative: a different secret over the same payload must not reproduce the vector.
    assert (
        sign_query("wrong", "a=1")
        != "310f57de49873563b85599a4aaa688883c5c6ebc7d3925020d99379d1a4d0af8"
    )


def test_negative_param_reorder_changes_signature():
    # Negative: HMAC covers the wire-order query string, so reordering must change the signature.
    assert sign_query("sec", "a=1&b=2") != sign_query("sec", "b=2&a=1")


def test_sign_query_within_latency_budget(perf_budget):
    # Perf: signing is pure CPU on a short payload; generous 5 ms budget per call.
    perf_budget.assert_within(
        lambda: sign_query("sec", "symbol=BTCUSDT&timestamp=1"), budget_ms=5.0
    )
