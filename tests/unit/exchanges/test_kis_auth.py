"""KIS OAuth2 토큰 캐싱 + tr_id 실전/모의 치환 로직 검증."""
import httpx
import pytest

from src.exchanges.kis.adapter import _KISHTTPClient


def _client(handler) -> _KISHTTPClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return _KISHTTPClient(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def test_resolve_tr_id_swaps_prefix_for_paper_trading():
    client = _client(lambda request: httpx.Response(200, json={}))
    assert client._resolve_tr_id("TTTC8434R") == "VTTC8434R"
    assert client._resolve_tr_id("JTTC0000R") == "VTTC0000R"


def test_resolve_tr_id_leaves_quote_endpoints_unchanged():
    client = _client(lambda request: httpx.Response(200, json={}))
    assert client._resolve_tr_id("FHKST01010100") == "FHKST01010100"


def test_resolve_tr_id_no_swap_for_real_trading():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    http_client = httpx.AsyncClient(
        base_url="https://openapi.koreainvestment.com:9443", transport=transport
    )
    client = _KISHTTPClient(
        "app", "secret", "12345678", "01", is_paper_trading=False, http_client=http_client
    )
    assert client._resolve_tr_id("TTTC8434R") == "TTTC8434R"


async def test_ensure_token_fetches_and_caches():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200, json={"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}
        )

    client = _client(handler)
    token1 = await client._ensure_token()
    token2 = await client._ensure_token()

    assert token1 == "tok-1"
    assert token2 == "tok-1"
    assert call_count == 1  # 캐싱됐으므로 두 번째 호출은 재요청 안 함


async def test_ensure_token_fails_fatally_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid appkey")

    client = _client(handler)
    from src.core.exceptions import FatalExchangeError

    with pytest.raises(FatalExchangeError):
        await client._ensure_token()
