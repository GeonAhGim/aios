"""Bitget 서명 알고리즘(2026-08-28 공식 문서 확인: prehash = timestamp +
method.upper() + requestPath(+쿼리스트링) + body, HMAC-SHA256 후 base64)이
정확히 구현됐는지 검증 — 독립적으로 계산한 값과 대조."""
from src.exchanges.bitget.adapter import _BitgetHTTPClient


def _client() -> _BitgetHTTPClient:
    return _BitgetHTTPClient("key", "test-secret", "passphrase")


def test_sign_matches_independently_computed_vector():
    client = _client()
    signature = client._sign(
        "1700000000000", "GET", "/api/v2/spot/market/tickers?symbol=BTCUSDT"
    )
    assert signature == "wJIbveegNzdZ6avEP4sw4uIecFce6See7NgWfNwOLF0="


def test_sign_changes_with_method():
    client = _client()
    get_sig = client._sign("1700000000000", "GET", "/api/v2/spot/market/tickers")
    post_sig = client._sign("1700000000000", "POST", "/api/v2/spot/market/tickers")
    assert get_sig != post_sig


def test_headers_include_demo_mode_marker():
    client = _BitgetHTTPClient("key", "secret", "passphrase", demo_mode=True)
    headers = client._headers("GET", "/api/v2/spot/market/tickers")
    assert headers["paptrading"] == "1"
    assert headers["ACCESS-KEY"] == "key"
    assert headers["ACCESS-PASSPHRASE"] == "passphrase"


def test_headers_omit_demo_marker_when_live():
    client = _BitgetHTTPClient("key", "secret", "passphrase", demo_mode=False)
    headers = client._headers("GET", "/api/v2/spot/market/tickers")
    assert "paptrading" not in headers
