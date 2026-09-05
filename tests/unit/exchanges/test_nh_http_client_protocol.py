"""PLT-40c QA(task-1142) — NH 믹스인 `self: NHHTTPClient` 계약의 런타임 배선 증명.

믹스인 메서드의 `self` 타입 주석은 mypy가 **호출 지점**에서만 검사한다 —
`NHAdapter`가 조립되는 adapter.py에서 `_act_no`/`_request`를 개명해도
src 안에 그 메서드를 `NHAdapter` 타입으로 직접 호출하는 코드가 없으면
(`ExchangeAdapter` 추상 타입으로만 호출) 정적으로 잡히지 않는다.
`@runtime_checkable` Protocol의 isinstance로 실제 조립체가 계약을
만족하는지, 그리고 계약이 진짜로 무언가를 거르는지(negative)를 고정한다.
"""
import httpx

from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.nh.adapter import NHAdapter


def _adapter() -> NHAdapter:
    client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443")
    return NHAdapter("appkey", "appsecret", "1234567890", http_client=client)


def test_nh_adapter_satisfies_nh_http_client_protocol() -> None:
    assert isinstance(_adapter(), NHHTTPClient)


def test_protocol_rejects_client_without_account_number() -> None:
    class _NoAccount:
        async def _request(self, method: str, path: str, *, params=None, body=None) -> dict:
            return {}

    assert not isinstance(_NoAccount(), NHHTTPClient)


def test_protocol_rejects_client_without_request() -> None:
    class _NoRequest:
        _act_no = "1234567890"

    assert not isinstance(_NoRequest(), NHHTTPClient)
