"""NHAdapter 통합 테스트 공용 헬퍼.

tests/integration/test_nh_adapter*.py 전체가 공유하는 어댑터 생성/요청 라우팅/
가드 우회 헬퍼만 모은다 — 업무 로직(엔드포인트/필드/에러매핑) 검증은 각 분할
파일(test_nh_adapter.py 토큰·에러처리·health, test_nh_adapter_market_data.py
market/account, test_nh_adapter_trading.py 주문, test_nh_adapter_websocket.py
WS)에 둔다.
"""

from decimal import Decimal

import httpx

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.nh.adapter import NHAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}


def _make_adapter(handler, *, is_paper_trading: bool = True) -> NHAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443", transport=transport)
    return NHAdapter(
        "appkey", "appsecret", "1234567890", is_paper_trading=is_paper_trading, http_client=client
    )


# task-1356(esc-1082 후속) — place/cancel/modify_order에 @require_paper_sandbox가
# 붙었고, NHAdapter.is_paper_trading/is_sandboxed는 생성자 인자와 무관하게
# 항상 False다(모듈 상단 test_is_paper_trading_and_sandboxed_are_always_false
# 참조) — 즉 이 3개 메서드를 "PAPER로 구성된 adapter"에서 호출하는 방법이
# 구조적으로 없다. 가드 자체가 항상 막는다는 것은
# test_live_guard_coverage.py::test_nh_*_rejects_adapter가 이미 검증하므로,
# 아래 업무 로직(엔드포인트/필드/에러매핑) 테스트는 데코레이터가 감싸기 전의
# 원본 함수(`__wrapped__`, functools.wraps가 자동으로 남긴다)를 직접 호출해
# 검증한다 — 소스의 가드를 우회하도록 고치는 게 아니라 테스트에서만 우회한다.
async def _unguarded_place_order(adapter: NHAdapter, order: Order) -> Order:
    return await NHAdapter.place_order.__wrapped__(adapter, order)  # type: ignore[attr-defined]  # functools.wraps가 남긴 __wrapped__(위 주석 참고, 데코레이터 우회는 테스트 전용)


async def _unguarded_cancel_order(adapter: NHAdapter, order_id: str) -> bool:
    return await NHAdapter.cancel_order.__wrapped__(adapter, order_id)  # type: ignore[attr-defined]  # functools.wraps가 남긴 __wrapped__(위 주석 참고, 데코레이터 우회는 테스트 전용)


async def _unguarded_modify_order(adapter: NHAdapter, order_id: str, **kwargs) -> Order:
    return await NHAdapter.modify_order.__wrapped__(adapter, order_id, **kwargs)  # type: ignore[attr-defined]  # functools.wraps가 남긴 __wrapped__(위 주석 참고, 데코레이터 우회는 테스트 전용)


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/token":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


def _success(output_0: dict | list | None = None, **extra_outputs) -> dict:
    body: dict = {"rsp_cd": "00000", "rsp_msg": "정상처리완료"}
    if output_0 is not None:
        body["Output_0"] = output_0
    body.update(extra_outputs)
    return body


def _order(**overrides) -> Order:
    defaults = dict(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="nh",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )
    defaults.update(overrides)
    return Order(**defaults)
