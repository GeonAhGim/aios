"""NHAdapter 통합 테스트.

실제 NH API 키가 없는 상태라 httpx.MockTransport로 공식 REST 요청 형태를
재현해 검증한다(test_kis_adapter.py와 동일 원칙). market_data/account/
trading의 요청·응답 필드명은 2026-09-03(task-114) 공식 OpenAPI 스펙
(`https://www.nhplug.com/openapi-docs/krstock/openapi.json`, 도메인이
정본임을 `nhplug-sdk` 레포 `docs/README.md`가 명시)을 직접 내려받아
확인한 값이다 — 02e_nh_api_spec_v1.md §3 참조. WebSocket 구독은
`nhplug/realtime.py` 공식 소스로 확인한 연결/구독/재연결 책임만
검증한다(데이터 프레임 필드 스키마는 여전히 미확인, websocket_mixin.py
모듈 docstring 참조).
"""
import json
from decimal import Decimal

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
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
    return await NHAdapter.place_order.__wrapped__(adapter, order)  # type: ignore[attr-defined]


async def _unguarded_cancel_order(adapter: NHAdapter, order_id: str) -> bool:
    return await NHAdapter.cancel_order.__wrapped__(adapter, order_id)  # type: ignore[attr-defined]


async def _unguarded_modify_order(adapter: NHAdapter, order_id: str, **kwargs) -> Order:
    return await NHAdapter.modify_order.__wrapped__(adapter, order_id, **kwargs)  # type: ignore[attr-defined]


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


# ---------- token issuance ----------


async def test_ensure_token_sends_form_params():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        assert request.url.params["appkey"] == "appkey"
        assert request.url.params["appsecretkey"] == "appsecret"
        assert request.url.params["grant_type"] == "client_credentials"
        return httpx.Response(200, json=TOKEN_RESPONSE)

    adapter = _make_adapter(handler)
    token = await adapter._ensure_token()

    assert token == "tok-1"


async def test_ensure_token_raises_fatal_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter._ensure_token()


# ---------- request-level error handling ----------


async def test_request_raises_retryable_on_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {"/krstock/quote/v1/currentPrice": lambda r: httpx.Response(200, text="<html/>")},
        )

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")


async def test_request_raises_retryable_on_business_failure_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/krstock/quote/v1/currentPrice": lambda r: httpx.Response(
                    200, json={"rsp_cd": "99999", "rsp_msg": "실패"}
                )
            },
        )

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")


async def test_request_treats_wanryo_message_as_success_even_with_unlisted_code():
    """SDK 관례 — rsp_cd가 알려진 성공코드 목록에 없어도 rsp_msg에
    "완료"가 포함되면 성공으로 취급한다(공식 nhplug/client.py::is_success()
    확인)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/krstock/quote/v1/currentPrice": lambda r: httpx.Response(
                    200,
                    json={
                        "rsp_cd": "ZZZZZ",
                        "rsp_msg": "처리 완료",
                        "Output_0": {"stck_prpr": "70000", "bidp": "69900", "askp": "70100"},
                    },
                )
            },
        )

    adapter = _make_adapter(handler)
    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")


# ---------- market data ----------


async def test_get_ticker_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/quote/v1/currentPrice"
        body = json.loads(request.content)
        assert body["iem_cd"] == "005930"
        return httpx.Response(
            200, json=_success({"stck_prpr": "70000", "bidp": "69900", "askp": "70100"})
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")
    assert ticker.bid == Decimal("69900")


async def test_get_ticker_raises_fatal_when_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected_field": "1"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("005930")


def _depth_output(**extra) -> dict:
    output = {"stck_prpr": "70000"}
    output.update(extra)
    return output


async def test_get_orderbook_parses_full_depth():
    output = _depth_output(
        askp1="70100", askp2="70200", askp_rsqn1="10", askp_rsqn2="20",
        bidp1="69900", bidp2="69800", bidp_rsqn1="30", bidp_rsqn2="40",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success(output))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    book = await adapter.get_orderbook("005930")

    assert len(book.bids) == 2
    assert len(book.asks) == 2
    assert book.bids[0].price == Decimal("69900")
    assert book.bids[0].quantity == Decimal("30")
    assert book.asks[0].price == Decimal("70100")
    assert book.asks[0].quantity == Decimal("10")


async def test_get_orderbook_raises_fatal_when_no_quote_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"stck_prpr": "70000"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_get_orderbook_raises_fatal_when_depth_quantity_missing():
    """askp1은 있는데 짝이 되는 잔량 askp_rsqn1이 없는 경우(비대칭 필드
    누락) — 조용히 quantity=0으로 채우지 않고 실패한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_success(_depth_output(askp1="70100", bidp1="69900", bidp_rsqn1="30"))
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_get_ohlcv_not_implemented():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.get_ohlcv("005930", "1d")


async def test_subscribe_ticker_stream_not_implemented():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.subscribe_ticker_stream("005930", lambda t: None)


async def test_capabilities_declare_websocket_unsupported():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.KR_EQUITY]
    assert caps.supports_websocket is False  # 데이터 메시지 필드 스키마 미확인
    assert caps.market_hours is not None


# ---------- account ----------


async def test_get_balance_maps_holdings_and_cash():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/inquiry/v1/balance"
        body = json.loads(request.content)
        assert body["act_no"] == "1234567890"
        return httpx.Response(
            200,
            json=_success(
                {"dca": "1000000"},
                Output_1=[{"iem_cd": "005930", "itg_bnc_qty": "10", "rsdl_qty": "8"}],
            ),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    balances = await adapter.get_balance()

    by_asset = {b.asset: b for b in balances}
    assert by_asset["005930"].total == Decimal("10")
    assert by_asset["005930"].available == Decimal("8")
    assert by_asset["KRW"].total == Decimal("1000000")


async def test_get_balance_raises_fatal_when_holding_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_success({"dca": "1000000"}, Output_1=[{"iem_cd": "005930"}]),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_balance()


async def test_get_positions_always_empty():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    assert await adapter.get_positions() == []


# ---------- trading: place_order ----------


async def test_place_order_buy_uses_cash_buy_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashBuy"
        body = json.loads(request.content)
        assert body["nmn_pr_tp_cd"] == "01"  # 지정가
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler})
    )
    order = _order()

    result = await _unguarded_place_order(adapter, order)

    assert result.exchange_order_id == "005930:999"
    assert result.status == OrderStatus.SUBMITTED


async def test_place_order_sell_uses_cash_sell_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashSell"
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashSell": handler})
    )
    order = _order(side=OrderSide.SELL)

    await _unguarded_place_order(adapter, order)


async def test_place_order_market_type_uses_market_division_code():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["nmn_pr_tp_cd"] == "05"  # 시장가
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler})
    )
    order = _order(order_type=OrderType.MARKET, price=None)

    await _unguarded_place_order(adapter, order)


async def test_place_order_raises_fatal_when_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected": "1"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler})
    )
    with pytest.raises(FatalExchangeError):
        await _unguarded_place_order(adapter, _order())


# ---------- trading: cancel_order ----------


async def test_cancel_order_sends_confirmed_cancel_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cancel"
        body = json.loads(request.content)
        assert body["org_mkt_orr_no"] == 999
        assert body["iem_cd"] == "005930"
        assert body["all_pat_dit_cd"] == "1"
        return httpx.Response(200, json=_success())

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cancel": handler})
    )
    assert await _unguarded_cancel_order(adapter, "005930:999") is True


async def test_cancel_order_returns_true_on_alternate_success_code():
    """회귀 테스트 — 이전 구현은 `rsp_cd == "00000"`만 성공으로 봐서
    "00166" 같은 다른 성공 코드에서도 취소를 실패(False)로 잘못 보고하는
    버그가 있었다(_request()가 이미 성공 판정을 끝냈으므로 여기 도달한
    것 자체가 성공)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "00166", "rsp_msg": "정상처리완료"})

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cancel": handler})
    )
    assert await _unguarded_cancel_order(adapter, "005930:999") is True


async def test_cancel_order_raises_retryable_on_business_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "이미 체결된 주문입니다"})

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cancel": handler})
    )
    with pytest.raises(RetryableExchangeError):
        await _unguarded_cancel_order(adapter, "005930:999")


async def test_cancel_order_raises_fatal_on_malformed_order_id():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_cancel_order(adapter, "no-separator")


async def test_cancel_order_raises_fatal_on_non_numeric_mkt_orr_no():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_cancel_order(adapter, "005930:not-a-number")


# ---------- trading: modify_order ----------


async def test_modify_order_sends_confirmed_modify_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/modify"
        body = json.loads(request.content)
        assert body["org_mkt_orr_no"] == 999
        assert body["iem_cd"] == "005930"
        assert body["cor_qty"] == "5"
        assert body["cor_pr"] == "71000"
        return httpx.Response(200, json=_success({"mkt_orr_no": 1000}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/modify": handler})
    )
    order = await _unguarded_modify_order(
        adapter, "005930:999", price=Decimal("71000"), size=Decimal("5")
    )

    assert order.exchange_order_id == "005930:1000"
    assert order.status == OrderStatus.ACKNOWLEDGED


async def test_modify_order_accepts_quantity_kwarg_for_backward_compat():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"mkt_orr_no": 1000}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/modify": handler})
    )
    order = await _unguarded_modify_order(
        adapter, "005930:999", price=Decimal("71000"), quantity=Decimal("5")
    )
    assert order.quantity == Decimal("5")


async def test_modify_order_raises_fatal_when_price_missing():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_modify_order(adapter, "005930:999", size=Decimal("5"))


async def test_modify_order_raises_fatal_when_size_missing():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await _unguarded_modify_order(adapter, "005930:999", price=Decimal("71000"))


async def test_modify_order_raises_retryable_on_business_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "실패"})

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/modify": handler})
    )
    with pytest.raises(RetryableExchangeError):
        await _unguarded_modify_order(
            adapter, "005930:999", price=Decimal("71000"), size=Decimal("5")
        )


async def test_modify_order_raises_fatal_when_response_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected": "1"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/modify": handler})
    )
    with pytest.raises(FatalExchangeError):
        await _unguarded_modify_order(
            adapter, "005930:999", price=Decimal("71000"), size=Decimal("5")
        )


# ---------- trading: get_order (confirmed structurally blocked) ----------


async def test_get_order_raises_not_implemented():
    """02e 스펙 §0-1/§3 — 공식 openapi.json으로 확인한 구조적 불일치
    (dailyOrderExecution 응답에 mkt_orr_no가 없음, trading_mixin.py 모듈
    docstring 참조) 때문에 근거 있는 구현이 불가능함을 검증한다."""
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.get_order("005930:999")


# ---------- misc ----------


async def test_is_paper_trading_and_sandboxed_are_always_false():
    """task-106 재확인 — 모의투자 도메인이 공식 문서상 "미제공"이라
    확인되기 전까지 항상 False(생성자 플래그와 무관, adapter.py 참조).
    Executor의 이중 검사(mode!=PAPER + 이 두 프로퍼티)가 이 신호로
    이 adapter의 실거래를 차단하는 것이 의도된 동작이다."""
    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )
    assert adapter.is_paper_trading is False
    assert adapter.is_sandboxed is False


# ---------- health check ----------


async def test_health_check_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"dca": "0"}, Output_1=[]))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    assert await adapter.health_check() is True


async def test_health_check_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "실패"})

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    assert await adapter.health_check() is False


# ---------- WebSocket 구독 (connect_and_subscribe) ----------
#
# 실제 소켓 대신 가짜 connect_fn을 주입해 결정적으로 재현한다(KIS WS 테스트와
# 동일 원칙, tests/integration/test_kis_websocket.py 참조).


class _StopTest(Exception):
    """무한 재연결 루프를 테스트 안에서 의도적으로 끊기 위한 표식 예외."""


class _FakeConnection:
    def __init__(self, messages, *, raise_after=None):
        self._messages = messages
        self._raise_after = raise_after
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._raise_after is not None:
            raise self._raise_after


class _FakeConnectCtx:
    def __init__(self, connection: _FakeConnection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_connect_and_subscribe_sends_confirmed_envelope():
    connection = _FakeConnection(
        [json.dumps({"header": {"tr_cd": "mc", "tr_key": "005930"}, "body": {"x": 1}})],
        raise_after=ConnectionClosed(None, None),
    )
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert url == "wss://moapi.nhplug.com:17070/websocket"  # 모의투자 URL
            return _FakeConnectCtx(connection)
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )

    received: list[str] = []

    async def on_raw_frame(raw: str) -> None:
        received.append(raw)

    with pytest.raises(_StopTest):
        await adapter.connect_and_subscribe("mc", "005930", on_raw_frame, connect_fn=connect_fn)

    subscribe_msg = json.loads(connection.sent[0])
    assert subscribe_msg["header"]["token"] == "tok-1"
    assert subscribe_msg["header"]["tr_type"] == "1"
    assert subscribe_msg["body"]["tr_cd"] == "mc"
    assert subscribe_msg["body"]["tr_key"] == "005930"
    assert len(received) == 1


async def test_connect_and_subscribe_uses_domestic_url_for_live_account():
    def connect_fn(url: str):
        assert url == "wss://api.nhplug.com:7070/websocket"
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=False
    )

    async def on_raw_frame(raw: str) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.connect_and_subscribe("mc", "005930", on_raw_frame, connect_fn=connect_fn)


async def test_connect_and_subscribe_uses_overseas_url_when_requested():
    def connect_fn(url: str):
        assert url == "wss://api.nhplug.com:7080/websocket"
        raise _StopTest

    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=False
    )

    async def on_raw_frame(raw: str) -> None:
        pass

    with pytest.raises(_StopTest):
        await adapter.connect_and_subscribe(
            "RC", "GIC123", on_raw_frame, is_domestic=False, connect_fn=connect_fn
        )


async def test_connect_and_subscribe_reconnects_after_disconnect():
    """연결이 끊기면(ConnectionClosed) 재연결을 시도한다(§2.1 재연결
    책임) — on_reconnecting/on_reconnected 훅이 두 번째 연결에서만
    호출되는지 확인한다."""
    first = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    second = _FakeConnection([], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}
    hooks: list[str] = []

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeConnectCtx(first)
        if call_count["n"] == 2:
            return _FakeConnectCtx(second)
        raise _StopTest

    async def on_reconnecting() -> None:
        hooks.append("reconnecting")

    async def on_reconnected() -> None:
        hooks.append("reconnected")

    async def on_raw_frame(raw: str) -> None:
        pass

    async def instant_sleep(_seconds: float) -> None:
        return None

    from src.exchanges.nh import websocket_mixin

    with pytest.raises(_StopTest):
        await websocket_mixin._run_nh_ws_subscription(
            "wss://moapi.nhplug.com:17070/websocket",
            {"header": {"token": "t", "tr_type": "1"}, "body": {"tr_cd": "mc", "tr_key": "x"}},
            on_raw_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
            sleep_fn=instant_sleep,
        )

    # 1차 연결(끊김) → 2차 연결(reconnecting→성공→reconnected, 끊김) →
    # 3차 시도 직전에 connect_fn이 _StopTest를 던짐(그 전에 reconnecting은
    # 이미 호출됨).
    assert hooks == ["reconnecting", "reconnected", "reconnecting"]
    assert call_count["n"] == 3
